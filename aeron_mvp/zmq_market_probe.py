#!/usr/bin/env python3
"""Receive a bounded YD Market Wire stream for transport acceptance testing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import zmq

from market_wire import SbeDecodeError, TOPIC, decode_market_quote


def build_summary(first, last, count: int) -> dict:
    return {
        "count": count,
        "first_sequence": first["AeronSequence"],
        "last_sequence": last["AeronSequence"],
        "session_id": last["AeronSessionID"],
        "contract": last["Contract"],
        "schema_version": last["SchemaVersion"],
        "source": last["MarketSource"],
        "first_tick": first,
        "last_tick": last,
    }


def receive(endpoint: str, expected_count: int) -> list[dict]:
    context = zmq.Context.instance()
    socket = context.socket(zmq.PULL)
    socket.setsockopt(zmq.LINGER, 0)
    socket.setsockopt(zmq.RCVHWM, 100_000)
    socket.connect(endpoint)
    received: list[dict] = []
    expected_by_session: dict[str, int] = {}
    try:
        while len(received) < expected_count:
            frames = socket.recv_multipart()
            if len(frames) != 2:
                raise SbeDecodeError(
                    f"expected 2 ZMQ frames, received {len(frames)}"
                )
            topic, payload = frames
            if topic != TOPIC:
                continue
            tick = decode_market_quote(payload)
            session_id = tick["AeronSessionID"]
            sequence = tick["AeronSequence"]
            expected = expected_by_session.get(session_id, sequence)
            if sequence != expected:
                raise RuntimeError(
                    f"sequence discontinuity session={session_id} "
                    f"expected={expected} actual={sequence}"
                )
            expected_by_session[session_id] = sequence + 1
            received.append(tick)
    finally:
        socket.close(linger=0)
    return received


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--expected-count", required=True, type=int)
    parser.add_argument("--summary-file", required=True, type=Path)
    args = parser.parse_args()
    if args.expected_count < 1:
        parser.error("--expected-count must be positive")
    if not args.endpoint.startswith("tcp://"):
        parser.error("--endpoint must use tcp://")

    ticks = receive(args.endpoint, args.expected_count)
    first, last = ticks[0], ticks[-1]
    if first["AeronSequence"] != 1 or last["AeronSequence"] != args.expected_count:
        raise RuntimeError(
            f"unexpected sequence range {first['AeronSequence']}..{last['AeronSequence']}"
        )
    summary = build_summary(first, last, len(ticks))
    args.summary_file.parent.mkdir(parents=True, exist_ok=True)
    args.summary_file.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "ZMQ_MARKET_PROBE result=SUCCESS "
        f"count={len(ticks)} sequence={first['AeronSequence']}..{last['AeronSequence']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
