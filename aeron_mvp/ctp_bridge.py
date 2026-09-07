#!/usr/bin/env python3
"""Receive OpenCTP ticks and forward a fixed binary envelope to the Java Aeron publisher."""

from __future__ import annotations

import argparse
import math
import signal
import socket
import struct
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openctp_ctp import thostmduserapi as mdapi

if __package__:
    from .ctp_cli import parse_args
else:
    from ctp_cli import parse_args


PACKET_V2 = struct.Struct("!IHHIQQQd" + "ddqq" * 5 + "32s16s32s")
PACKET = struct.Struct(
    "!IHHIQQQd" + "ddqq" * 5 + "q" + "d" * 15 + "H32s16s32s16s16s16s"
)
MAGIC = 0x43545031
VERSION = 3
TIMESTAMP_VALID = 1
DEPTH_LEVELS = 5
CHINA_TZ = timezone(timedelta(hours=8))


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


def market_timestamp(
    tick: object, local_receive_ns: int | None = None, *, live: bool = False
) -> tuple[int, str, bool]:
    action_day = str(getattr(tick, "ActionDay", "") or "").strip()
    trading_day = str(getattr(tick, "TradingDay", "") or "").strip()
    date_text = action_day if len(action_day) == 8 else trading_day
    update_time = str(getattr(tick, "UpdateTime", "") or "").strip()
    millis = nonnegative_integer(getattr(tick, "UpdateMillisec", 0))
    raw = f"{date_text} {update_time}.{millis:03d}"
    try:
        parsed = datetime.strptime(
            f"{date_text} {update_time}", "%Y%m%d %H:%M:%S"
        ).replace(tzinfo=CHINA_TZ, microsecond=millis * 1_000)
        if live and local_receive_ns is not None:
            received = datetime.fromtimestamp(local_receive_ns / 1_000_000_000, CHINA_TZ)
            if parsed - received > timedelta(hours=12):
                # Some night-session feeds put the next trading date in ActionDay.
                # Only infer a calendar date for an explicitly LIVE source, and only
                # when the received time-of-day is within one minute. Raw ActionDay
                # and TradingDay are still encoded unchanged in their own fields.
                base = received.replace(hour=parsed.hour, minute=parsed.minute,
                                        second=parsed.second, microsecond=parsed.microsecond)
                candidates = [base + timedelta(days=offset) for offset in (-1, 0, 1)]
                nearest = min(candidates, key=lambda item: abs(item - received))
                if (action_day and action_day != trading_day) or abs(nearest - received) > timedelta(minutes=1):
                    return 0, raw, False
                parsed = nearest
                raw = f"{parsed:%Y%m%d} {update_time}.{millis:03d}"
        return int(parsed.timestamp() * 1_000_000_000), raw, True
    except (TypeError, ValueError, OverflowError):
        return 0, raw, False


class CtpMarketBridge(mdapi.CThostFtdcMdSpi):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self.args = args
        self.api = None
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.destination = (args.udp_host, args.udp_port)
        self.sequence = 0
        self.callbacks = 0
        self.packed_packets = 0
        self.sent_packets = 0
        self.publish_lock = threading.Lock()
        self.last_tick_monotonic = time.monotonic()
        self.connected = False
        self.logged_in = False
        self.stop = threading.Event()
        self.subscription_lock = threading.Lock()
        self.pending_subscriptions = []
        self.subscription_buffers = []
        self.next_subscription_at = 0.0

    def run(self) -> None:
        flow_path = Path(self.args.flow_path)
        flow_path.mkdir(parents=True, exist_ok=True)
        self.api = mdapi.CThostFtdcMdApi.CreateFtdcMdApi(f"{flow_path}/")
        self.api.RegisterFront(self.args.front)
        self.api.RegisterSpi(self)
        self.api.Init()

    def close(self) -> None:
        self.stop.set()
        if self.api is not None:
            self.api.RegisterSpi(None)
            self.api.Release()
            self.api = None
        self.socket.close()
        self.log_counters()

    def log_counters(self) -> None:
        print(
            f"CTP_BRIDGE state=COUNTERS timestamp_ns={time.time_ns()} "
            f"callbacks={self.callbacks} packed_packets={self.packed_packets} "
            f"udp_sent_packets={self.sent_packets}", flush=True,
        )

    def OnFrontConnected(self) -> None:
        self.connected = True
        print(f"CTP_BRIDGE state=CONNECTED front={self.args.front}", flush=True)
        request = mdapi.CThostFtdcReqUserLoginField()
        result = self.api.ReqUserLogin(request, 1)
        print(f"CTP_BRIDGE state=LOGIN_REQUEST result={result}", flush=True)

    def OnFrontDisconnected(self, reason: int) -> None:
        with self.subscription_lock:
            self.connected = False
            self.logged_in = False
            self.pending_subscriptions.clear()
        print(f"CTP_BRIDGE state=DISCONNECTED reason={reason}", flush=True)

    def OnRspUserLogin(self, response, info, request_id: int, is_last: bool) -> None:
        error_id = int(getattr(info, "ErrorID", 0) or 0) if info is not None else 0
        if error_id != 0:
            raise RuntimeError(
                f"CTP login failed error_id={error_id} "
                f"message={getattr(info, 'ErrorMsg', '')}"
            )
        self.logged_in = True
        trading_day = str(getattr(response, "TradingDay", "") or "")
        encoded = [item.encode("utf-8") for item in self.args.instruments]
        # Both TTS and the live front need paced requests outside SDK callbacks.
        # Keep the encoded buffers alive for native asynchronous processing.
        with self.subscription_lock:
            self.subscription_buffers = [[item] for item in encoded]
            self.pending_subscriptions = list(self.subscription_buffers)
            self.next_subscription_at = time.monotonic() + 0.5
        print(
            f"CTP_BRIDGE state=SUBSCRIBE_QUEUED trading_day={trading_day} "
            f"instruments={','.join(self.args.instruments)} mode=paced-single",
            flush=True,
        )

    def poll_subscriptions(self) -> None:
        """Send at most one queued CTP request per interval outside SDK callbacks."""
        with self.subscription_lock:
            now = time.monotonic()
            if (self.stop.is_set() or not self.connected or not self.logged_in
                    or not self.pending_subscriptions or now < self.next_subscription_at):
                return
            encoded = self.pending_subscriptions.pop(0)
            self.next_subscription_at = now + 0.5
        result = self.api.SubscribeMarketData(encoded, 1)
        print(
            f"CTP_BRIDGE state=SUBSCRIBE_REQUEST instrument={encoded[0].decode('utf-8')} "
            f"result={result} mode=paced-single", flush=True,
        )
        if result != 0:
            raise RuntimeError(f"CTP subscription request failed result={result}")

    def OnRspSubMarketData(self, instrument, info, request_id: int, is_last: bool) -> None:
        error_id = int(getattr(info, "ErrorID", 0) or 0) if info is not None else 0
        instrument_id = str(getattr(instrument, "InstrumentID", "") or "")
        print(
            f"CTP_BRIDGE state=SUBSCRIBED instrument={instrument_id} "
            f"error_id={error_id} is_last={bool(is_last)}",
            flush=True,
        )

    def OnRtnDepthMarketData(self, tick) -> None:
        with self.publish_lock:
            self._publish_tick(tick)

    def _publish_tick(self, tick) -> None:
        self.callbacks += 1
        local_receive_ns = time.time_ns()
        market_ns, market_raw, timestamp_valid = market_timestamp(
            tick, local_receive_ns,
            live=getattr(self.args, "latency_mode", "historical_replay") == "live",
        )
        self.sequence += 1
        self.last_tick_monotonic = time.monotonic()
        instrument = getattr(tick, "InstrumentID", "")
        exchange_id = getattr(tick, "ExchangeID", "")
        trading_day = getattr(tick, "TradingDay", "")
        action_day = getattr(tick, "ActionDay", "")
        update_time = getattr(tick, "UpdateTime", "")
        packet = PACKET.pack(
            MAGIC,
            VERSION,
            (TIMESTAMP_VALID if timestamp_valid else 0) | (DEPTH_LEVELS << 8),
            self.args.repeat,
            self.sequence,
            market_ns,
            local_receive_ns,
            finite_number(getattr(tick, "LastPrice", 0.0)),
            *(
                value
                for level in range(1, DEPTH_LEVELS + 1)
                for value in (
                    finite_number(getattr(tick, f"BidPrice{level}", 0.0)),
                    finite_number(getattr(tick, f"AskPrice{level}", 0.0)),
                    nonnegative_integer(getattr(tick, f"BidVolume{level}", 0)),
                    nonnegative_integer(getattr(tick, f"AskVolume{level}", 0)),
                )
            ),
            nonnegative_integer(getattr(tick, "Volume", 0)),
            finite_number(getattr(tick, "Turnover", 0.0)),
            finite_number(getattr(tick, "OpenInterest", 0.0)),
            finite_number(getattr(tick, "PreSettlementPrice", 0.0)),
            finite_number(getattr(tick, "PreClosePrice", 0.0)),
            finite_number(getattr(tick, "PreOpenInterest", 0.0)),
            finite_number(getattr(tick, "OpenPrice", 0.0)),
            finite_number(getattr(tick, "HighestPrice", 0.0)),
            finite_number(getattr(tick, "LowestPrice", 0.0)),
            finite_number(getattr(tick, "ClosePrice", 0.0)),
            finite_number(getattr(tick, "SettlementPrice", 0.0)),
            finite_number(getattr(tick, "UpperLimitPrice", 0.0)),
            finite_number(getattr(tick, "LowerLimitPrice", 0.0)),
            finite_number(getattr(tick, "PreDelta", 0.0)),
            finite_number(getattr(tick, "CurrDelta", 0.0)),
            finite_number(getattr(tick, "AveragePrice", 0.0)),
            min(999, nonnegative_integer(getattr(tick, "UpdateMillisec", 0))),
            fixed_utf8(instrument, 32),
            fixed_utf8(trading_day, 16),
            fixed_utf8(market_raw, 32),
            fixed_utf8(exchange_id, 16),
            fixed_utf8(action_day, 16),
            fixed_utf8(update_time, 16),
        )
        self.packed_packets += 1
        sent = self.socket.sendto(packet, self.destination)
        if sent == len(packet):
            self.sent_packets += 1
        if self.sequence == 1 or self.sequence % 100 == 0:
            self.log_counters()
            print(
                f"CTP_BRIDGE state=FORWARDING source_ticks={self.sequence} "
                f"instrument={instrument} market_time={market_raw} repeat={self.args.repeat}",
                flush=True,
            )


def run(args: argparse.Namespace) -> int:
    bridge = CtpMarketBridge(args)

    def stop_handler(signum, frame) -> None:
        bridge.stop.set()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    bridge.run()
    started = time.monotonic()
    try:
        while not bridge.stop.wait(0.1):
            bridge.poll_subscriptions()
            reference = bridge.last_tick_monotonic if bridge.sequence else started
            if time.monotonic() - reference > args.idle_timeout_seconds:
                raise TimeoutError(
                    f"no CTP tick received for {args.idle_timeout_seconds:.0f} seconds"
                )
    finally:
        bridge.close()
    if bridge.sequence == 0:
        print("CTP_BRIDGE result=INCOMPLETE source_ticks=0", flush=True)
        return 1
    print(f"CTP_BRIDGE result=SUCCESS source_ticks={bridge.sequence}", flush=True)
    return 0


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
