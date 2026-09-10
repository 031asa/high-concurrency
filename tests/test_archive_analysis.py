import importlib.util
import json
import struct
import statistics
from pathlib import Path
from datetime import datetime
import pytest

SPEC = importlib.util.spec_from_file_location("archive_analysis", Path(__file__).parents[1] / "ydcore/archive_analysis.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def ns(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=module.CHINA).timestamp()) * module.NS


def frame(seq, received, market, source="live", valid=True):
    block = bytearray(324)
    struct.pack_into("<QQQd", block, 0, seq, market, received, 123.5)
    block[64] = int(valid)
    strings = ["session", "IF2609", "20990101", "raw-time", "CFFEX", "20260907", "09:00:00", source]
    body = struct.pack("<HHHH", 324, 1, 701, 3) + block
    for text in strings:
        value = text.encode()
        body += struct.pack("<H", len(value)) + value
    length = 32 + len(body)
    header = bytearray(32)
    struct.pack_into("<iBBH", header, 0, length, 0, 192, 1)
    return header + body + bytes((-length) % 32)


def make_run(tmp_path, frames):
    run = tmp_path / "run"
    (run / "archive").mkdir(parents=True)
    (run / "run.meta").write_text("source=multi\nsources=live,tts,waiting\nsource_latency_mode.live=live\nsource_latency_mode.tts=historical_replay\n")
    (run / "archive/0-0.rec").write_bytes(b"".join(frames) + bytes(128))
    return run


def test_statistics_exclusions_and_replay(tmp_path):
    received = ns("2026-09-07T09:00:00")
    values = [frame(1, received, received-10_000_000), frame(2, received, received-30_000_000),
              frame(3, received, ns("2026-09-06T09:00:00")),
              frame(4, received, ns("2026-09-08T09:00:00")),
              frame(5, received, 0, valid=False),
              frame(6, received, received-20_000_000, "tts"),
              frame(7, received+61*module.NS, received+60*module.NS)]
    run = make_run(tmp_path, values + [values[-1]])
    original = (run / "archive/0-0.rec").read_bytes()
    report = module.analyse(run, "2026-09-07")
    live = report["sources"]["live"]
    assert live["same_day_latency"]["count"] == 3
    assert live["same_day_latency"]["mean_ms"] == pytest.approx(1040/3)
    assert live["same_day_latency"]["p95_ms"] == 1000
    assert live["same_day_latency"]["std_ms"] == pytest.approx(statistics.pstdev([10, 30, 1000]))
    assert live["categories"] == {"valid": 3, "old_snapshot": 1, "future_date": 1, "invalid_time": 1}
    assert live["session_total_latency"]["count"] == 5
    assert live["gaps_over_60s_count"] == 1
    assert live["latest_quote"]["last_price"] == 123.5
    assert report["duplicates"] == 1
    assert report["sources"]["tts"]["same_day_latency"]["mean_ms"] is None
    assert report["sources"]["waiting"]["archived_records"] == 0
    assert (run / "archive/0-0.rec").read_bytes() == original


def test_date_filter_and_sequence_scope(tmp_path):
    r = ns("2026-09-07T00:00:00")
    run = make_run(tmp_path, [frame(1, r-module.NS, r-2*module.NS), frame(3, r, r-module.NS)])
    report = module.analyse(run, "2026-09-07")
    assert report["missing_sequence_positions"] == 1
    assert report["sources"]["live"]["archived_records"] == 1
    assert report["sources"]["live"]["same_day_latency"]["count"] == 0
    assert report["sources"]["live"]["session_total_latency"]["count"] == 2


def test_unsupported_frame_fails_and_output_protection(tmp_path):
    run = make_run(tmp_path, [frame(1, 1, 1)])
    rec = run / "archive/0-0.rec"
    data = bytearray(rec.read_bytes())
    data[5] = 128
    rec.write_bytes(data)
    with pytest.raises(ValueError, match="fragmented"):
        module.analyse(run)
    with pytest.raises(SystemExit):
        module.main(["--run-dir", str(run), "--output", str(run / "run.meta")])
