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


def market_timestamp(tick: object) -> tuple[int, str, bool]:
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
        self.last_tick_monotonic = time.monotonic()
        self.connected = False
        self.logged_in = False
        self.stop = threading.Event()

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

    def OnFrontConnected(self) -> None:
        self.connected = True
        print(f"CTP_BRIDGE state=CONNECTED front={self.args.front}", flush=True)
        request = mdapi.CThostFtdcReqUserLoginField()
        result = self.api.ReqUserLogin(request, 1)
        print(f"CTP_BRIDGE state=LOGIN_REQUEST result={result}", flush=True)

    def OnFrontDisconnected(self, reason: int) -> None:
        self.connected = False
        self.logged_in = False
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
        result = self.api.SubscribeMarketData(encoded, len(encoded))
        print(
            f"CTP_BRIDGE state=SUBSCRIBE_REQUEST trading_day={trading_day} "
            f"instruments={','.join(self.args.instruments)} result={result}",
            flush=True,
        )

    def OnRspSubMarketData(self, instrument, info, request_id: int, is_last: bool) -> None:
        error_id = int(getattr(info, "ErrorID", 0) or 0) if info is not None else 0
        instrument_id = str(getattr(instrument, "InstrumentID", "") or "")
        print(
            f"CTP_BRIDGE state=SUBSCRIBED instrument={instrument_id} "
            f"error_id={error_id} is_last={bool(is_last)}",
            flush=True,
        )

    def OnRtnDepthMarketData(self, tick) -> None:
        local_receive_ns = time.time_ns()
        market_ns, market_raw, timestamp_valid = market_timestamp(tick)
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
        self.socket.sendto(packet, self.destination)
        if self.sequence == 1 or self.sequence % 100 == 0:
            print(
                f"CTP_BRIDGE state=FORWARDING source_ticks={self.sequence} "
                f"instrument={instrument} market_time={market_raw} repeat={self.args.repeat}",
                flush=True,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--front", default="tcp://trading.openctp.cn:30011")
    parser.add_argument("--instruments", required=True)
    parser.add_argument("--udp-host", default="127.0.0.1")
    parser.add_argument("--udp-port", type=int, default=24001)
    parser.add_argument("--repeat", type=int, default=10_000)
    parser.add_argument("--flow-path", required=True)
    parser.add_argument("--idle-timeout-seconds", type=float, default=60.0)
    args = parser.parse_args()
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


def main() -> int:
    args = parse_args()
    bridge = CtpMarketBridge(args)

    def stop_handler(signum, frame) -> None:
        bridge.stop.set()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    bridge.run()
    started = time.monotonic()
    try:
        while not bridge.stop.wait(0.1):
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


if __name__ == "__main__":
    raise SystemExit(main())
