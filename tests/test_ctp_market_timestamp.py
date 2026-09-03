"""Night-session calendar dates must not turn live latency into a 24-hour gap."""
import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace

stub_mdapi = ModuleType("openctp_ctp.thostmduserapi")
stub_mdapi.CThostFtdcMdSpi = type("StubMdSpi", (), {})
stub_package = ModuleType("openctp_ctp")
stub_package.thostmduserapi = stub_mdapi
sys.modules.setdefault("openctp_ctp", stub_package)
sys.modules.setdefault("openctp_ctp.thostmduserapi", stub_mdapi)
from aeron_mvp import ctp_bridge, ctp_cli


def ns(text):
    return int(datetime.fromisoformat(text).timestamp() * 1_000_000_000)


def night_tick(day="20260904", update="22:04:00", millis=500):
    return SimpleNamespace(
        InstrumentID="i2701", ActionDay=day, TradingDay=day,
        UpdateTime=update, UpdateMillisec=millis, LastPrice=724.5,
    )


def test_live_night_date_uses_receive_calendar_but_replay_does_not():
    tick = night_tick()
    received = ns("2026-09-03T22:04:00.600+08:00")
    live_ns, raw, valid = ctp_bridge.market_timestamp(tick, received, live=True)
    assert valid and live_ns == ns("2026-09-03T22:04:00.500+08:00")
    assert raw == "20260903 22:04:00.500"
    replay_ns, raw, valid = ctp_bridge.market_timestamp(tick, received)
    assert valid and replay_ns == ns("2026-09-04T22:04:00.500+08:00")
    assert raw == "20260904 22:04:00.500"
    assert tick.ActionDay == tick.TradingDay == "20260904"


def test_live_weekend_and_midnight_boundaries():
    tick = night_tick("20260907")
    value, _, valid = ctp_bridge.market_timestamp(
        tick, ns("2026-09-04T22:04:00.600+08:00"), live=True)
    assert valid and value == ns("2026-09-04T22:04:00.500+08:00")
    tick = night_tick("20260907", "23:59:59", 900)
    value, _, valid = ctp_bridge.market_timestamp(
        tick, ns("2026-09-05T00:00:00.100+08:00"), live=True)
    assert valid and value == ns("2026-09-04T23:59:59.900+08:00")


def test_normal_or_old_calendar_dates_are_not_moved():
    tick = night_tick("20260903")
    tick.TradingDay = "20260904"
    expected = ns("2026-09-03T22:04:00.500+08:00")
    assert ctp_bridge.market_timestamp(
        tick, expected + 100_000_000, live=True)[0] == expected
    # Do not make a stale historical tick look like current live data.
    assert ctp_bridge.market_timestamp(
        tick, ns("2026-09-04T22:04:00.600+08:00"), live=True)[0] == expected


def test_ambiguous_future_time_is_invalid_instead_of_fabricated():
    tick = night_tick(update="20:00:00")
    value, _, valid = ctp_bridge.market_timestamp(
        tick, ns("2026-09-03T22:04:00.600+08:00"), live=True)
    assert value == 0 and not valid


def test_v3_retains_raw_business_dates(monkeypatch):
    monkeypatch.setattr(ctp_bridge.time, "time_ns",
                        lambda: ns("2026-09-03T22:04:00.600+08:00"))
    bridge = ctp_bridge.CtpMarketBridge(SimpleNamespace(
        udp_host="127.0.0.1", udp_port=24001, repeat=1, latency_mode="live",
    ))
    bridge.socket.close()
    packets = []
    bridge.socket = SimpleNamespace(
        sendto=lambda packet, destination: packets.append(packet), close=lambda: None,
    )
    try:
        bridge.OnRtnDepthMarketData(night_tick())
    finally:
        bridge.close()
    values = ctp_bridge.PACKET.unpack(packets[0])
    assert values[5] == ns("2026-09-03T22:04:00.500+08:00")
    assert values[46].split(b"\0")[0] == b"20260904"  # Raw TradingDay.
    assert values[49].split(b"\0")[0] == b"20260904"  # Raw ActionDay.


def test_cli_requires_explicit_live_mode_for_date_inference(tmp_path):
    args = ["--instruments", "i2701", "--flow-path", str(tmp_path)]
    assert ctp_cli.parse_args(args).latency_mode == "historical_replay"
    assert ctp_cli.parse_args(args + ["--latency-mode", "live"]).latency_mode == "live"
