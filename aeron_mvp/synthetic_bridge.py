#!/usr/bin/env python3
"""Generate deterministic v3 UDP quotes for the multi-source ingress path."""

from __future__ import annotations

import argparse
import math
import socket
import struct
import time
from datetime import datetime, timezone


PACKET = struct.Struct(
    "!IHHIQQQd" + "ddqq" * 5 + "q" + "d" * 15 + "H32s16s32s16s16s16s"
)
MAGIC = 0x43545031
VERSION = 3
TIMESTAMP_VALID = 1
DEPTH_LEVELS = 5


def fixed_utf8(value: str, width: int) -> bytes:
    encoded = value.encode("utf-8")[: width - 1]
    return encoded + b"\0" * (width - len(encoded))


def build_packet(sequence: int, repeat: int, instrument: str) -> bytes:
    receive_ns = time.time_ns()
    market_ns = receive_ns - (sequence % 50 + 1) * 1_000_000
    now = datetime.fromtimestamp(market_ns / 1_000_000_000, timezone.utc)
    day = now.strftime("%Y%m%d")
    market_raw = now.strftime("%Y%m%d %H:%M:%S.%f")[:23]
    last_price = 5_000.0 + sequence * 0.01
    return PACKET.pack(
        MAGIC,
        VERSION,
        TIMESTAMP_VALID | (DEPTH_LEVELS << 8),
        repeat,
        sequence,
        market_ns,
        receive_ns,
        last_price,
        *(
            value
            for level in range(1, 6)
            for value in (
                last_price - level * 0.2,
                last_price + level * 0.2,
                10 + level,
                20 + level,
            )
        ),
        sequence * 10,
        last_price * sequence * 10,
        98_000.0 + sequence,
        4_980.0,
        4_990.0,
        98_000.0,
        4_995.0,
        max(5_010.0, last_price),
        min(4_985.0, last_price),
        last_price,
        last_price,
        5_500.0,
        4_500.0,
        0.1,
        0.2,
        last_price,
        int((market_ns // 1_000_000) % 1000),
        fixed_utf8(instrument, 32),
        fixed_utf8(day, 16),
        fixed_utf8(market_raw, 32),
        fixed_utf8("SIM", 16),
        fixed_utf8(day, 16),
        fixed_utf8(now.strftime("%H:%M:%S"), 16),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--udp-host", default="127.0.0.1")
    parser.add_argument("--udp-port", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--repeat", type=int, default=1_000)
    parser.add_argument("--instrument", default="IC2609")
    parser.add_argument("--interval-ms", type=float, default=1.0)
    args = parser.parse_args()
    if args.count < 1 or args.repeat < 1:
        parser.error("--count and --repeat must be positive")

    destination = (args.udp_host, args.udp_port)
    packets = math.ceil(args.count / args.repeat)
    sent = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as output:
        for index in range(packets):
            expanded = min(args.repeat, args.count - sent)
            packet = build_packet(index + 1, expanded, args.instrument)
            if output.sendto(packet, destination) != len(packet):
                raise RuntimeError("short synthetic UDP send")
            sent += expanded
            if args.interval_ms:
                time.sleep(args.interval_ms / 1_000)
    print(
        f"SYNTHETIC_BRIDGE result=SUCCESS source_ticks={packets} published={sent}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
