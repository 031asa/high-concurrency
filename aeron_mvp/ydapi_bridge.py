#!/usr/bin/env python3
"""Forward YDApi market-data callbacks to the Java Aeron UDP publisher."""

from __future__ import annotations

import argparse
import json
import math
import re
import signal
import socket
import struct
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


PACKET = struct.Struct("!IHHIQQQd" + "ddqq" * 5 + "32s16s32s")
MAGIC = 0x43545031
VERSION = 2
TIMESTAMP_VALID = 1
DEPTH_LEVELS = 1
CHINA_TZ = timezone(timedelta(hours=8))
TRADING_DAY_START_MS = 17 * 60 * 60 * 1000
DAY_MS = 24 * 60 * 60 * 1000
YD_CLOCK_PATTERN = re.compile(
    r"^(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<fraction>\d{1,9}))?$"
)


def fixed_utf8(value: object, width: int) -> bytes:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value or "")
    encoded = text.encode("utf-8")[: width - 1]
    return encoded + b"\0" * (width - len(encoded))


def finite_number(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) and abs(number) < 1.0e100 else 0.0


def nonnegative_integer(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def parse_yd_timestamp_ms(value: object) -> int:
    if not isinstance(value, str):
        raise TypeError(f"YDApi timestamp must be a string: {value!r}")
    match = YD_CLOCK_PATTERN.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid YDApi timestamp: {value!r}")
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second"))
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"invalid YDApi timestamp: {value!r}")
    fraction = match.group("fraction") or ""
    milliseconds = int((fraction + "000")[:3])
    clock_ms = ((hour * 60 * 60 + minute * 60 + second) * 1000) + milliseconds
    return (clock_ms - TRADING_DAY_START_MS) % DAY_MS


def local_cycle_timestamp_ms(local_receive_ns: int) -> int:
    received_at = datetime.fromtimestamp(local_receive_ns / 1_000_000_000, CHINA_TZ)
    clock_ms = (
        (received_at.hour * 60 * 60 + received_at.minute * 60 + received_at.second) * 1000
        + received_at.microsecond // 1000
    )
    return (clock_ms - TRADING_DAY_START_MS) % DAY_MS


def signed_timestamp_difference_ms(local_ms: int, market_ms: int) -> int:
    difference = int(local_ms) - int(market_ms)
    half_day_ms = DAY_MS // 2
    if difference > half_day_ms:
        difference -= DAY_MS
    elif difference < -half_day_ms:
        difference += DAY_MS
    return difference


def market_timestamp_ns(raw_timestamp: object, local_receive_ns: int) -> tuple[int, bool]:
    try:
        market_ms = parse_yd_timestamp_ms(raw_timestamp)
    except (TypeError, ValueError):
        return 0, False
    local_ms = local_cycle_timestamp_ms(local_receive_ns)
    latency_ms = signed_timestamp_difference_ms(local_ms, market_ms)
    return local_receive_ns - latency_ms * 1_000_000, True


class YdApiUdpPublisher:
    """A non-blocking loopback publisher called directly by the vendor callback."""

    def __init__(self, udp_host: str, udp_port: int, repeat: int) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.destination = (udp_host, udp_port)
        self.repeat = repeat
        self.sequence = 0
        self.last_tick_monotonic = time.monotonic()
        self.lock = threading.Lock()

    def publish(self, market_data: object, local_receive_ns: int | None = None) -> bool:
        received_ns = time.time_ns() if local_receive_ns is None else local_receive_ns
        raw_timestamp = getattr(market_data, "timestamp", None)
        market_ns, timestamp_valid = market_timestamp_ns(raw_timestamp, received_ns)
        with self.lock:
            self.sequence += 1
            sequence = self.sequence
            self.last_tick_monotonic = time.monotonic()
        packet = PACKET.pack(
            MAGIC,
            VERSION,
            (TIMESTAMP_VALID if timestamp_valid else 0) | (DEPTH_LEVELS << 8),
            self.repeat,
            sequence,
            market_ns,
            received_ns,
            finite_number(getattr(market_data, "last_price", 0.0)),
            finite_number(getattr(market_data, "bid_price", 0.0)),
            finite_number(getattr(market_data, "ask_price", 0.0)),
            nonnegative_integer(getattr(market_data, "bid_volume", 0)),
            nonnegative_integer(getattr(market_data, "ask_volume", 0)),
            *(0 for _ in range(16)),
            fixed_utf8(getattr(market_data, "instrument", ""), 32),
            fixed_utf8(getattr(market_data, "tradingday", ""), 16),
            fixed_utf8(raw_timestamp, 32),
        )
        try:
            return self.socket.sendto(packet, self.destination) == len(packet)
        except (BlockingIOError, InterruptedError, OSError):
            return False

    def close(self) -> None:
        self.socket.close()


class YdApiListener:
    def __init__(self, publisher: YdApiUdpPublisher) -> None:
        self.publisher = publisher
        self.login_error = None
        self.caughtup_event = threading.Event()
        self.failure_event = threading.Event()
        self.failure_message = ""
        self.subscribed_instrument = None
        self.ignored_callbacks = 0

    def login(self, error, max_order_ref, is_monitor) -> None:
        self.login_error = error
        result = "SUCCESS" if error == 0 else "FAILED"
        print(f"YDAPI_BRIDGE state=LOGIN result={result} error={error}", flush=True)

    def caughtup(self) -> None:
        self.caughtup_event.set()
        print("YDAPI_BRIDGE state=CAUGHTUP", flush=True)

    def enable_instrument(self, instrument: str) -> None:
        self.subscribed_instrument = instrument

    def disable_instrument(self) -> None:
        self.subscribed_instrument = None

    def marketdata_is_enabled(self, market_data) -> bool:
        instrument = str(getattr(market_data, "instrument", "") or "")
        return (
            self.subscribed_instrument is not None
            and instrument == self.subscribed_instrument
        )

    def marketdata(self, market_data) -> None:
        received_ns = time.time_ns()
        if not self.marketdata_is_enabled(market_data):
            self.ignored_callbacks += 1
            if self.ignored_callbacks == 1:
                print(
                    "YDAPI_BRIDGE state=IGNORING_PRE_SUBSCRIPTION_CALLBACKS",
                    flush=True,
                )
            return
        if not self.publisher.publish(market_data, received_ns):
            self.failure_message = "non-blocking UDP send failed"
            self.failure_event.set()
            return
        sequence = self.publisher.sequence
        if sequence == 1 or sequence % 100 == 0:
            print(
                "YDAPI_BRIDGE state=FORWARDING "
                f"source_ticks={sequence} "
                f"instrument={getattr(market_data, 'instrument', '')} "
                f"market_time={getattr(market_data, 'timestamp', '')} "
                f"repeat={self.publisher.repeat}",
                flush=True,
            )


def load_account(path: str) -> tuple[str, str]:
    with Path(path).open("r", encoding="utf-8") as handle:
        account = json.load(handle)
    name = account.get("name")
    password = account.get("password")
    if not isinstance(name, str) or not name or not isinstance(password, str) or not password:
        raise ValueError("account config requires non-empty name and password")
    return name, password


def mask_account(account: str) -> str:
    if len(account) <= 4:
        return "*" * len(account)
    return f"{account[:2]}***{account[-2:]}"


def create_api(listener: YdApiListener, account: str, password: str, api_config: str):
    from pyyd import YDApi

    return YDApi(listener, account, password, api_config)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--account-config", required=True)
    parser.add_argument("--api-config", required=True)
    parser.add_argument("--udp-host", default="127.0.0.1")
    parser.add_argument("--udp-port", type=int, default=24001)
    parser.add_argument("--repeat", type=int, default=10_000)
    parser.add_argument("--startup-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--idle-timeout-seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    if not 1 <= args.udp_port <= 65535:
        parser.error("--udp-port must be between 1 and 65535")
    if not 1 <= args.repeat <= 1_000_000:
        parser.error("--repeat must be between 1 and 1000000")
    if args.startup_timeout_seconds <= 0 or args.idle_timeout_seconds <= 0:
        parser.error("timeouts must be positive")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    account, password = load_account(args.account_config)
    publisher = YdApiUdpPublisher(args.udp_host, args.udp_port, args.repeat)
    listener = YdApiListener(publisher)
    api = create_api(listener, account, password, args.api_config)
    stop = threading.Event()
    subscribed = False

    def stop_handler(signum, frame) -> None:
        stop.set()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    try:
        result = api.start()
        if result is False:
            raise RuntimeError("YDApi.start() returned False")
        deadline = time.monotonic() + args.startup_timeout_seconds
        while not listener.caughtup_event.wait(0.1):
            if listener.login_error not in (None, 0):
                raise RuntimeError(f"YDApi login failed: {listener.login_error}")
            if time.monotonic() >= deadline:
                raise TimeoutError("waiting for YDApi caughtup timed out")
        if api.get_instrument(args.instrument) is None:
            raise ValueError(f"instrument not found in YDApi: {args.instrument}")
        # Enable immediately before subscribe so a synchronous subscription callback
        # is accepted, while startup/catch-up callbacks remain excluded.
        listener.enable_instrument(args.instrument)
        if api.subscribe(args.instrument) is False:
            listener.disable_instrument()
            raise RuntimeError(f"YDApi.subscribe() returned False: {args.instrument}")
        subscribed = True
        print(
            "YDAPI_BRIDGE state=SUBSCRIBED "
            f"account={mask_account(account)} instrument={args.instrument}",
            flush=True,
        )
        subscribed_at = time.monotonic()
        while not stop.wait(0.1):
            if listener.failure_event.is_set():
                raise RuntimeError(listener.failure_message)
            reference = publisher.last_tick_monotonic if publisher.sequence else subscribed_at
            if time.monotonic() - reference > args.idle_timeout_seconds:
                raise TimeoutError(
                    f"no YDApi tick received for {args.idle_timeout_seconds:.0f} seconds"
                )
    finally:
        if subscribed:
            try:
                api.unsubscribe(args.instrument)
            except Exception as exc:
                print(f"YDAPI_BRIDGE state=UNSUBSCRIBE_FAILED type={type(exc).__name__}", flush=True)
        listener.disable_instrument()
        stop_api = getattr(api, "stop", None)
        if callable(stop_api):
            stop_api()
        publisher.close()
    if publisher.sequence == 0:
        print("YDAPI_BRIDGE result=INCOMPLETE source_ticks=0", flush=True)
        return 1
    print(f"YDAPI_BRIDGE result=SUCCESS source_ticks={publisher.sequence}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
