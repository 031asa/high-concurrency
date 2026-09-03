import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "config" / "market-sources"
UNIT_DIR = PROJECT_ROOT / "deploy" / "systemd"


def test_continuous_ctp_sources_are_independent_and_depth_capable():
    tts = json.loads((SOURCE_DIR / "ctp-tts-7x24.json").read_text(encoding="utf-8"))
    live = json.loads((SOURCE_DIR / "ctp-live-5level.json").read_text(encoding="utf-8"))

    assert tts["name"] == "ctp-tts-7x24"
    assert tts["front"] == "tcp://118.184.178.137:30011"
    assert tts["api_kind"] == "tts"
    assert tts["latency_mode"] == "historical_replay"
    assert live["name"] == "ctp-live-5level"
    assert live["front"] == "tcp://180.169.112.53:42213"
    assert live["api_kind"] == "official"
    assert live["latency_mode"] == "live"
    assert tts["instruments"] == ["IF2609", "IC2609", "IH2609", "IM2609", "au2612", "ag2612"]
    assert len(live["instruments"]) == 11  # Live and replay have independent contracts.
    assert any(contract.startswith("au") for contract in live["instruments"])
    assert tts["source_timeout_seconds"] == 86400
    assert live["source_timeout_seconds"] == 86400


def test_aeron_accepts_the_same_day_long_timeout_as_source_configs():
    source = (PROJECT_ROOT / "aeron_mvp" / "src" / "com" / "ydtrader" / "mvp" / "AeronMvp.java").read_text(encoding="utf-8")

    assert '"source-timeout-seconds", 60L, 1L, 86_400L' in source
    assert '"timeout-seconds", 30, 1, 86_400' in source
    assert "new double[expectedCount]" not in source
    assert "latencyHistogram = new DoubleHistogram(3)" in source


def test_continuous_launcher_passes_both_source_configs_as_one_list():
    launcher = (PROJECT_ROOT / "scripts" / "run_dual_ctp_service.sh").read_text(
        encoding="utf-8"
    )

    tts = launcher.index("ctp-tts-7x24.json")
    live = launcher.index("ctp-live-5level.json")
    assert tts < live
    assert "run_multi_source_aeron_mvp.sh" in launcher
    assert "YDTRADER_CONTINUOUS_COUNT" in launcher
    assert "synthetic" not in launcher


def test_systemd_stack_restarts_both_long_running_services():
    market = (UNIT_DIR / "ydtrader-market.service").read_text(encoding="utf-8")
    dashboard = (UNIT_DIR / "ydtrader-dashboard.service").read_text(encoding="utf-8")
    target = (UNIT_DIR / "ydtrader-stack.target").read_text(encoding="utf-8")

    assert "run_dual_ctp_service.sh" in market
    assert "run_dashboard_service.sh" in dashboard
    for service in (market, dashboard):
        assert "Restart=always" in service
        assert "PartOf=ydtrader-stack.target" in service
        assert "@PROJECT_ROOT@" in service
    assert "Wants=ydtrader-market.service ydtrader-dashboard.service" in target
    assert "StartLimitIntervalSec=0" in market
    assert "RestartSec=15" in market


def test_desktop_launcher_uses_the_single_stack_manager():
    launcher = (
        PROJECT_ROOT / "deploy" / "windows" / "start-ydtrader-dashboard.cmd"
    ).read_text(encoding="utf-8")

    assert "Ubuntu-24.04" in launcher
    assert "scripts/ydtrader_stack.sh install-start" in launcher
    assert "http://127.0.0.1:8080/" in launcher


def test_windows_launcher_has_native_line_endings_and_readiness_check():
    raw = (PROJECT_ROOT / "deploy" / "windows" / "start-ydtrader-dashboard.cmd").read_bytes()
    text = raw.decode("ascii")
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert "Invoke-WebRequest" in text
    assert text.index("Invoke-WebRequest") < text.index('start ""')
    assert 'if errorlevel 1 goto failed' in text
    assert "*.cmd text eol=crlf" in (PROJECT_ROOT / ".gitattributes").read_text()
