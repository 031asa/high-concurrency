"""Receive OpenCTP ticks and forward a fixed binary envelope to the Java Aeron publisher."""

import argparse


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--front", default="tcp://trading.openctp.cn:30011")
    parser.add_argument("--instruments", required=True)
    parser.add_argument("--udp-host", default="127.0.0.1")
    parser.add_argument("--udp-port", type=int, default=24001)
    parser.add_argument("--repeat", type=int, default=10_000)
    parser.add_argument("--flow-path", required=True)
    parser.add_argument("--idle-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--latency-mode", choices=("live", "historical_replay"),
                        default="historical_replay")
    args = parser.parse_args(argv)
    args.instruments = [item.strip() for item in args.instruments.split(",") if item.strip()]
    if not args.instruments:
        parser.error("--instruments must contain at least one contract")
    if not 1 <= args.udp_port <= 65535:
        parser.error("--udp-port must be between 1 and 65535")
    if not 1 <= args.repeat <= 1_000_000:
        parser.error("--repeat must be between 1 and 1000000")
    if args.idle_timeout_seconds <= 0:
        parser.error("--idle-timeout-seconds must be positive")
    return args


def main(argv=None) -> int:
    # Parse first: --help and invalid arguments must never load the native CTP SDK.
    args = parse_args(argv)
    from .ctp_bridge import run

    return run(args)
