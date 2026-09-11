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
from dataclasses import dataclass, field
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
class PacketReorderBuffer:
    source: str = "unknown"
    wait_ms: int = 100
    capacity: int = 1024
    expected: int = 1
    pending: dict = field(default_factory=dict)
    reordered_packets: int = 0
    recovered_gaps: int = 0

    def check_timeout(self, now: float) -> None:
        if self.pending and now - min(t for _, t in self.pending.values()) >= self.wait_ms / 1000:
            self.fail(min(self.pending), "reorder_timeout")

    def fail(self, actual: int, reason: str) -> None:
        raise RuntimeError(
            f"source sequence discontinuity source={self.source} "
            f"expected={self.expected} actual={actual} reason={reason} "
            f"buffered={len(self.pending)} wait_ms={self.wait_ms} capacity={self.capacity}"
        )

    def offer(self, sequence: int, packet: bytes, now: float) -> list[bytes]:
        self.check_timeout(now)
        if sequence < self.expected or sequence in self.pending:
            self.fail(sequence, "duplicate_or_stale")
        if sequence > self.expected:
            if len(self.pending) >= self.capacity:
                self.fail(sequence, "reorder_capacity")
            if not self.pending:
                print(f"MULTI_SOURCE_MUX state=REORDER_WAIT source={self.source} "
                      f"next_sequence={self.expected} received_sequence={sequence}", flush=True)
            self.pending[sequence] = (packet, now)
            self.reordered_packets += 1
            return []
        waiting = bool(self.pending)
        ready = [packet]
        self.expected += 1
        while self.expected in self.pending:
            ready.append(self.pending.pop(self.expected)[0])
            self.expected += 1
        if waiting and not self.pending:
            self.recovered_gaps += 1
            print(f"MULTI_SOURCE_MUX state=REORDER_RECOVERED source={self.source} "
                  f"next_sequence={self.expected} recovered_gaps={self.recovered_gaps}", flush=True)
        return ready


@dataclass
class InputState:
    spec: InputSpec
    socket: socket.socket
    remaining: int
    expected_sequence: int = 1
    source_ticks: int = 0
    published: int = 0
    last_packet_at: float = 0.0
    received_packets: int = 0
    reorder: PacketReorderBuffer = field(default_factory=PacketReorderBuffer)


def log_counters(states: list[InputState]) -> None:
    timestamp_ns = time.time_ns()
    for state in states:
        print(
            f"MULTI_SOURCE_MUX state=COUNTERS timestamp_ns={timestamp_ns} "
            f"source={state.spec.source} udp_received_packets={state.received_packets} "
            f"udp_sent_packets={state.source_ticks} expanded_records={state.published} "
            f"reorder_buffered={len(state.reorder.pending)} "
            f"reordered_packets={state.reorder.reordered_packets} "
            f"recovered_gaps={state.reorder.recovered_gaps}",
            flush=True,
        )


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


def idle_timeout_message(state: InputState, timeout_seconds: float) -> str:
    return (
        f"source {state.spec.source} idle for {timeout_seconds:g} seconds after "
        f"source_ticks={state.source_ticks} published={state.published} "
        f"remaining={state.remaining}"
    )


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
            state.reorder = PacketReorderBuffer(spec.source,
                getattr(args, "reorder_wait_ms", 100), getattr(args, "reorder_max_packets", 1024))
            state.last_packet_at = started
            states.append(state)
            selector.register(source_socket, selectors.EVENT_READ, state)
        _write_ready(args.ready_file)
        print(
            "MULTI_SOURCE_MUX state=READY "
            f"inputs={','.join(item.spec.source for item in states)} "
            f"output={args.output_host}:{args.output_port} "
            f"count_per_source={args.count_per_source} "
            f"idle_timeout_seconds={args.source_timeout_seconds:g}",
            flush=True,
        )

        global_sequence = 0
        next_counters_at = started + 5.0
        while any(state.remaining for state in states):
            events = selector.select(timeout=min(0.01, getattr(args, "reorder_wait_ms", 100) / 1000))
            now = time.monotonic()
            if now >= next_counters_at:
                log_counters(states)
                next_counters_at = now + 5.0
            for state in states:
                if state.remaining:
                    state.reorder.check_timeout(now)
                if state.remaining and now - state.last_packet_at > args.source_timeout_seconds:
                    raise TimeoutError(idle_timeout_message(state, args.source_timeout_seconds))
            for key, _ in events:
                state: InputState = key.data
                packet, _ = state.socket.recvfrom(2048)
                state.received_packets += 1
                state.last_packet_at = now
                if not state.remaining:
                    continue
                _, source_sequence, _ = tag_packet(
                    packet,
                    state.spec.source,
                    global_sequence + 1,
                    state.remaining,
                )
                for ordered in state.reorder.offer(source_sequence, packet, now):
                    if not state.remaining:
                        break
                    tagged, _, expanded = tag_packet(ordered, state.spec.source,
                                                     global_sequence + 1, state.remaining)
                    try:
                        sent = output.sendto(tagged, destination)
                    except (BlockingIOError, InterruptedError, OSError) as exc:
                        raise RuntimeError(f"UDP output failed for source {state.spec.source}") from exc
                    if sent != len(tagged):
                        raise RuntimeError(f"short UDP output for source {state.spec.source}: {sent}/{len(tagged)}")
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
        log_counters(states)
        selector.close()
        for state in states:
            state.socket.close()
        output.close()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True, help="source=UDP_PORT")
    parser.add_argument("--bind-host", default="127.0.0.1")
    parser.add_argument("--output-host", default="127.0.0.1")
    parser.add_argument("--output-port", type=int, required=True)
    parser.add_argument("--count-per-source", type=int, required=True)
    parser.add_argument("--source-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--reorder-wait-ms", type=int, default=100)
    parser.add_argument("--reorder-max-packets", type=int, default=1024)
    args = parser.parse_args(argv)
    if not 1 <= args.reorder_wait_ms <= 60_000 or not 1 <= args.reorder_max_packets <= 65_536:
        parser.error("reorder wait must be 1..60000 ms and capacity 1..65536 packets")
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


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
