#!/usr/bin/env python3
"""Merge one or more loopback UDP market sources into one ordered adapter stream."""

from __future__ import annotations

import argparse
import os
import re
import selectors
import socket
import struct
import time
from dataclasses import dataclass
from pathlib import Path


MAGIC = 0x43545031
SOURCE_WIDTH = 32
HEADER = struct.Struct("!IHHIQQQ")
PACKET_SIZES = {1: 156, 2: 284, 3: 462}
TAGGED_PACKET_SIZES = {version: size + SOURCE_WIDTH for version, size in PACKET_SIZES.items()}
SOURCE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


@dataclass(frozen=True)
class InputSpec:
    source: str
    port: int


@dataclass
class InputState:
    spec: InputSpec
    socket: socket.socket
    remaining: int
    expected_sequence: int = 1
    source_ticks: int = 0
    published: int = 0
    last_packet_at: float = 0.0


def fixed_source(value: str) -> bytes:
    encoded = value.encode("ascii")
    return encoded + b"\0" * (SOURCE_WIDTH - len(encoded))


def parse_input_specs(values: list[str]) -> list[InputSpec]:
    specs: list[InputSpec] = []
    names: set[str] = set()
    ports: set[int] = set()
    for value in values:
        try:
            name, port_text = value.split("=", 1)
            port = int(port_text)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid input specification: {value}") from exc
        if not SOURCE_PATTERN.fullmatch(name):
            raise ValueError(f"invalid source name: {name}")
        if not 1 <= port <= 65535:
            raise ValueError(f"invalid UDP port for {name}: {port}")
        if name in names:
            raise ValueError(f"duplicate source name: {name}")
        if port in ports:
            raise ValueError(f"duplicate UDP port: {port}")
        names.add(name)
        ports.add(port)
        specs.append(InputSpec(name, port))
    if not specs:
        raise ValueError("at least one input is required")
    return specs


def tag_packet(
    packet: bytes,
    source: str,
    global_sequence: int,
    remaining: int,
) -> tuple[bytes, int, int]:
    if len(packet) < HEADER.size:
        raise ValueError(f"packet is shorter than header: {len(packet)}")
    magic, version, flags, repeat, source_sequence, market_ns, receive_ns = HEADER.unpack_from(packet)
    expected_size = PACKET_SIZES.get(version)
    if magic != MAGIC or expected_size != len(packet):
        raise ValueError(
            f"invalid source packet magic={magic:#x} version={version} size={len(packet)}"
        )
    if repeat < 1 or remaining < 1:
        raise ValueError(f"invalid repeat/remaining: repeat={repeat} remaining={remaining}")
    expanded = min(repeat, remaining)
    tagged = bytearray(packet)
    HEADER.pack_into(
        tagged,
        0,
        magic,
        4,
        flags,
        expanded,
        global_sequence,
        market_ns,
        receive_ns,
    )
    tagged.extend(fixed_source(source))
    return bytes(tagged), source_sequence, expanded


def _write_ready(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text("READY\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    specs = parse_input_specs(args.input)
    selector = selectors.DefaultSelector()
    states: list[InputState] = []
    output = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    output.setblocking(False)
    destination = (args.output_host, args.output_port)
    started = time.monotonic()
    try:
        for spec in specs:
            source_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            source_socket.bind((args.bind_host, spec.port))
            source_socket.setblocking(False)
            state = InputState(spec, source_socket, args.count_per_source)
            state.last_packet_at = started
            states.append(state)
            selector.register(source_socket, selectors.EVENT_READ, state)
        _write_ready(args.ready_file)
        print(
            "MULTI_SOURCE_MUX state=READY "
            f"inputs={','.join(item.spec.source for item in states)} "
            f"output={args.output_host}:{args.output_port} "
            f"count_per_source={args.count_per_source}",
            flush=True,
        )

        global_sequence = 0
        while any(state.remaining for state in states):
            events = selector.select(timeout=0.05)
            now = time.monotonic()
            for state in states:
                if state.remaining and now - state.last_packet_at > args.source_timeout_seconds:
                    raise TimeoutError(
                        f"source {state.spec.source} produced no packet for "
                        f"{args.source_timeout_seconds:g} seconds"
                    )
            for key, _ in events:
                state: InputState = key.data
                packet, _ = state.socket.recvfrom(2048)
                state.last_packet_at = now
                if not state.remaining:
                    continue
                tagged, source_sequence, expanded = tag_packet(
                    packet,
                    state.spec.source,
                    global_sequence + 1,
                    state.remaining,
                )
                if source_sequence != state.expected_sequence:
                    raise RuntimeError(
                        f"source sequence discontinuity source={state.spec.source} "
                        f"expected={state.expected_sequence} actual={source_sequence}"
                    )
                try:
                    sent = output.sendto(tagged, destination)
                except (BlockingIOError, InterruptedError, OSError) as exc:
                    raise RuntimeError(
                        f"UDP output failed for source {state.spec.source}"
                    ) from exc
                if sent != len(tagged):
                    raise RuntimeError(
                        f"short UDP output for source {state.spec.source}: {sent}/{len(tagged)}"
                    )
                global_sequence += 1
                state.expected_sequence += 1
                state.source_ticks += 1
                state.published += expanded
                state.remaining -= expanded

        counts = ",".join(f"{state.spec.source}:{state.published}" for state in states)
        print(
            f"MULTI_SOURCE_MUX result=SUCCESS total={sum(item.published for item in states)} "
            f"sources={counts}",
            flush=True,
        )
        return 0
    finally:
        selector.close()
        for state in states:
            state.socket.close()
        output.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True, help="source=UDP_PORT")
    parser.add_argument("--bind-host", default="127.0.0.1")
    parser.add_argument("--output-host", default="127.0.0.1")
    parser.add_argument("--output-port", type=int, required=True)
    parser.add_argument("--count-per-source", type=int, required=True)
    parser.add_argument("--source-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--ready-file", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.output_port <= 65535:
        parser.error("--output-port must be between 1 and 65535")
    if args.count_per_source < 1:
        parser.error("--count-per-source must be positive")
    if args.source_timeout_seconds <= 0:
        parser.error("--source-timeout-seconds must be positive")
    try:
        parse_input_specs(args.input)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main() -> int:
    return run(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
