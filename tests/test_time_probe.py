import json
from pathlib import Path
import subprocess
from unittest.mock import patch
import pytest
from scripts.time_probe import probe, save, history

def test_probe_read_only_and_sign():
    with patch("scripts.time_probe.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 0, "", "System clock wrong by -0.125 seconds")
        row = probe("192.0.2.1", 50)
    assert row["offset_ms"] == -125
    assert row["status"] == "EXCEEDED"
    assert "-Q" in row["command"] and "-q" not in row["command"]
    assert row["adjusts_clock"] is False
    assert row["rtt_ms"] is None
    assert row["uncertainty_ms"] is None

@pytest.mark.parametrize("failure", [FileNotFoundError("chronyd"), subprocess.TimeoutExpired("chronyd", 25)])
def test_failures_are_null(failure):
    with patch("scripts.time_probe.subprocess.run", side_effect=failure):
        row = probe("192.0.2.1", 50)
    assert row["offset_ms"] is None and row["status"] == "ERROR"

def test_invalid_output():
    with patch("scripts.time_probe.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "No sample")):
        assert probe("192.0.2.1", 50)["offset_ms"] is None

def test_archive_daily_groups_failures_and_restart(tmp_path):
    with patch("scripts.time_probe.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "System clock wrong by 0.010 seconds")):
        row = probe("192.0.2.1", 50)
    day = row["date_beijing"]
    save(tmp_path, row)
    second = dict(row, probe_id="second", offset_ms=30, status="PASS")
    save(tmp_path, second)
    save(tmp_path, dict(row, probe_id="failed", offset_ms=None, status="ERROR"))
    save(tmp_path, dict(row, probe_id="host2", hostname="other", offset_ms=999))
    save(tmp_path, dict(row, probe_id="old", date_beijing="2026-01-01"))
    (tmp_path / day / "broken.json").write_text("{")
    result = history(tmp_path, day)
    group = next(g for g in result["groups"] if g["hostname"] == row["hostname"])
    assert group["mean_ms"] == 20 and group["max_abs_ms"] == 30
    assert group["valid"] == 2 and group["failed"] == 1
    assert result["unreadable_files"] == 1 and len(result["rows"]) == 4
    assert len(history(tmp_path, "2026-01-01")["rows"]) == 1
    assert history(tmp_path, "2026-01-02")["groups"] == []
    assert json.loads((tmp_path / day / (row["probe_id"] + ".json")).read_text()) == row

@pytest.mark.parametrize("day", ["../secret", "2026-02-30", "20260907"])
def test_reject_invalid_date(tmp_path, day):
    with pytest.raises(ValueError):
        history(tmp_path, day)

def test_invalid_server():
    with pytest.raises(ValueError):
        probe("server iburst\nmakestep 1 1", 50)

def test_clock_history_http(tmp_path):
    import threading
    import urllib.request
    import urllib.error
    from http.server import ThreadingHTTPServer
    from dashboard.server import DashboardHandler
    with patch.object(DashboardHandler, "clock_history_root", tmp_path):
        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = "http://127.0.0.1:" + str(server.server_port)
            with urllib.request.urlopen(url + "/api/clock-history?date=2026-01-02") as response:
                result = json.load(response)
                assert result["date"] == "2026-01-02" and result["rows"] == []
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(url + "/api/clock-history?date=bad")
            assert error.value.code == 400
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

def test_existing_evidence_not_overwritten(tmp_path):
    row = {"date_beijing": "2026-01-02", "probe_id": "unique", "value": 1}
    target = save(tmp_path, row)
    with pytest.raises(FileExistsError):
        save(tmp_path, dict(row, value=2))
    assert json.loads(target.read_text())["value"] == 1

