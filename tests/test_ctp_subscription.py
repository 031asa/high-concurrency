"""CTP pacing regression: both replay and live sources use deferred subscriptions."""
import sys
from types import ModuleType, SimpleNamespace
import pytest

stub_mdapi = ModuleType("openctp_ctp.thostmduserapi")
stub_mdapi.CThostFtdcMdSpi = type("StubMdSpi", (), {})
stub_package = ModuleType("openctp_ctp")
stub_package.thostmduserapi = stub_mdapi
sys.modules.setdefault("openctp_ctp", stub_package)
sys.modules.setdefault("openctp_ctp.thostmduserapi", stub_mdapi)
from aeron_mvp import ctp_bridge


@pytest.fixture
def subscription_bridge(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(ctp_bridge.time, "monotonic", lambda: clock[0])
    bridge = ctp_bridge.CtpMarketBridge(SimpleNamespace(
        udp_host="127.0.0.1", udp_port=24001, repeat=1,
        instruments=["IF2609", "IC2609"],
    ))
    calls = []
    bridge.api = SimpleNamespace(
        SubscribeMarketData=lambda items, count: calls.append((items, count)) or 0,
    )
    bridge.connected = True
    yield bridge, clock, calls
    bridge.api = None
    bridge.close()


def login(bridge):
    bridge.OnRspUserLogin(SimpleNamespace(TradingDay="20260903"), None, 1, True)


def test_tts_defers_and_paces_each_configured_contract(subscription_bridge):
    bridge, clock, calls = subscription_bridge
    login(bridge)
    assert calls == []  # No sleeping/network requests in the login callback.
    bridge.poll_subscriptions()
    assert calls == []
    clock[0] = 0.51
    bridge.poll_subscriptions()
    assert calls == [([b"IF2609"], 1)]
    clock[0] = 0.7
    bridge.poll_subscriptions()
    assert len(calls) == 1
    clock[0] = 1.02
    bridge.poll_subscriptions()
    assert calls == [([b"IF2609"], 1), ([b"IC2609"], 1)]
    clock[0] = 2.0
    bridge.poll_subscriptions()
    assert len(calls) == 2


def test_tts_disconnect_clears_pending_and_relogin_requeues(subscription_bridge):
    bridge, clock, calls = subscription_bridge
    login(bridge)
    bridge.OnFrontDisconnected(4097)
    clock[0] = 1.0
    bridge.poll_subscriptions()
    assert calls == []
    assert bridge.pending_subscriptions == []
    bridge.connected = True
    login(bridge)
    clock[0] = 1.51
    bridge.poll_subscriptions()
    assert calls == [([b"IF2609"], 1)]


def test_official_ctp_uses_paced_subscription(subscription_bridge, monkeypatch, tmp_path):
    bridge, clock, calls = subscription_bridge
    native_api = bridge.api
    native_api.RegisterFront = lambda front: None
    native_api.RegisterSpi = lambda spi: None
    native_api.Init = lambda: None
    bridge.args.flow_path = str(tmp_path)
    bridge.args.front = "tcp://180.169.112.53:42213"
    monkeypatch.setattr(ctp_bridge.mdapi, "CThostFtdcMdApi", SimpleNamespace(
        GetApiVersion=lambda: "v6.7.11_20250617",
        CreateFtdcMdApi=lambda path: native_api,
    ), raising=False)
    bridge.run()
    login(bridge)
    assert calls == []
    clock[0] = 0.51
    bridge.poll_subscriptions()
    assert calls == [([b"IF2609"], 1)]
    clock[0] = 1.02
    bridge.poll_subscriptions()
    assert calls == [([b"IF2609"], 1), ([b"IC2609"], 1)]


def test_tts_stop_does_not_send_more_requests(subscription_bridge):
    bridge, clock, calls = subscription_bridge
    login(bridge)
    bridge.stop.set()
    clock[0] = 1.0
    bridge.poll_subscriptions()
    assert calls == []


def test_tts_request_failure_is_not_silently_accepted(subscription_bridge):
    bridge, clock, calls = subscription_bridge
    bridge.api.SubscribeMarketData = lambda items, count: -1
    login(bridge)
    clock[0] = 1.0
    with pytest.raises(RuntimeError, match="subscription request failed"):
        bridge.poll_subscriptions()


def test_keepalive_is_single_instance_and_stops_with_target():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts/ydtrader_stack.sh").read_text()
    launcher = (root / "deploy/windows/start-ydtrader-dashboard.cmd").read_text()
    assert 'flock -n 9 || return 0' in script
    assert 'while systemctl --user is-active --quiet "$TARGET"' in script
    assert "scripts/ydtrader_stack.sh hold" in launcher
    assert "-WindowStyle Hidden" in launcher
