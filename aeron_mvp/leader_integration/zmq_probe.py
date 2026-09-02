#!/usr/bin/env python3
"""Receive a bounded Leader ZMQ stream for cross-language acceptance testing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import ModuleType


def install_hpquant_stub() -> None:
    hpquant = ModuleType("hpquant")
    message = ModuleType("hpquant.message")
    bus = ModuleType("hpquant.message.zmq_bus")
    bus.ZmqSubscriber = type("ZmqSubscriber", (), {})
    message.zmq_bus = bus
    hpquant.message = message
    sys.modules["hpquant"] = hpquant
    sys.modules["hpquant.message"] = message
    sys.modules["hpquant.message.zmq_bus"] = bus


class CountingQueue:
    def __init__(self) -> None:
        self.count = 0
        self.first = None
        self.last = None

    def put(self, item) -> None:
        if self.first is None:
            self.first = item
        self.last = item
        self.count += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--expected-count", required=True, type=int)
    parser.add_argument("--summary-file", required=True, type=Path)
    args = parser.parse_args()
    if args.expected_count < 1:
        parser.error("--expected-count must be positive")

    install_hpquant_stub()
    from hpquant_aeron_source import AeronTickSubscriber

    sink = CountingQueue()
    subscriber = AeronTickSubscriber(args.endpoint, sink)
    try:
        while sink.count < args.expected_count:
            subscriber.receive_once()
    finally:
        subscriber.close()

    first = sink.first
    last = sink.last
    if first["AeronSequence"] != 1 or last["AeronSequence"] != args.expected_count:
        raise RuntimeError(
            f"unexpected sequence range {first['AeronSequence']}..{last['AeronSequence']}"
        )
    summary = {
        "count": sink.count,
        "first_sequence": first["AeronSequence"],
        "last_sequence": last["AeronSequence"],
        "session_id": last["AeronSessionID"],
        "contract": last["Contract"],
        "schema_version": last["SchemaVersion"],
        "source": last["MarketSource"],
    }
    args.summary_file.parent.mkdir(parents=True, exist_ok=True)
    args.summary_file.write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "LEADER_ZMQ_PROBE result=SUCCESS "
        f"count={sink.count} sequence={first['AeronSequence']}..{last['AeronSequence']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
