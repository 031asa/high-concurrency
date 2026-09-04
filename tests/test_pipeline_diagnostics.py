from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_java_timeout_diagnostics_include_progress_counts():
    publisher = (
        ROOT / "aeron_mvp" / "src" / "com" / "ydtrader" / "mvp" / "AeronMvp.java"
    ).read_text(encoding="utf-8")
    egress = (
        ROOT
        / "aeron_mvp"
        / "src"
        / "com"
        / "ydtrader"
        / "mvp"
        / "ZmqMarketDataEgress.java"
    ).read_text(encoding="utf-8")

    assert '" seconds after source_ticks=" + sourceTicks' in publisher
    assert '" published=" + published' in publisher
    assert '" remaining=" + (count - published)' in publisher
    assert '" seconds after sent=" + forwarder.sent' in egress
    assert '" expected=" + expectedCount' in egress
