"""Opt-in: run ONLY in a private network namespace (Aeron uses fixed ports).

Set YDTRADER_ISOLATED_REORDER_TEST=1 after isolating the test network.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aeron_mvp.synthetic_bridge import build_packet


@pytest.mark.skipif(os.environ.get("YDTRADER_ISOLATED_REORDER_TEST") != "1",
                    reason="requires private network namespace")
@pytest.mark.parametrize("through_mux", [False, True])
def test_actual_reorder_archive_audit_replay(tmp_path, through_mux):
    root = Path(__file__).resolve().parents[1]
    java = ["bash", str(root / "aeron_mvp/run_java.sh")]
    processes, files = [], []

    def start(name, command):
        handle = (tmp_path / (name + ".log")).open("w")
        files.append(handle)
        child = subprocess.Popen(command, cwd=root, stdout=handle, stderr=subprocess.STDOUT)
        processes.append(child)
        return child

    def ready(path, child):
        deadline = time.monotonic() + 15
        while not path.exists():
            assert child.poll() is None, list(tmp_path.glob("*.log"))
            assert time.monotonic() < deadline, "readiness timeout"
            time.sleep(.02)

    try:
        aeron = str(tmp_path / "aeron")
        server_ready = tmp_path / "server.ready"
        server = start("server", java + ["server", "--aeron-dir", aeron,
            "--archive-dir", str(tmp_path / "archive"), "--ready-file", str(server_ready)])
        ready(server_ready, server)
        recording = tmp_path / "recording"
        publisher = start("publisher", java + ["publish-adapter", "--aeron-dir", aeron,
            "--recording-file", str(recording), "--count", "6", "--udp-port", "24001",
            "--source-timeout-seconds", "10"])
        ready(recording, publisher)
        port = 24001
        if through_mux:
            port = 24002
            mux_ready = tmp_path / "mux.ready"
            mux = start("mux", [sys.executable, "-m", "aeron_mvp.multi_source_mux",
                "--input", "test=24002", "--output-port", "24001",
                "--count-per-source", "6", "--ready-file", str(mux_ready)])
            ready(mux_ready, mux)
        packets = {i: build_packet(i, 1, "IF2609") for i in range(1, 7)}
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            for i in [1, 3, 2, 4, 6, 5]:
                sender.sendto(packets[i], ("127.0.0.1", port))
                time.sleep(.002)
        assert publisher.wait(timeout=15) == 0
        if through_mux:
            assert mux.wait(timeout=15) == 0
        recording_id = recording.read_text().strip()
        for label, mode in [("compute", "compute"), ("audit", "audit"), ("replay", "compute")]:
            child = start(label, java + [mode, "--aeron-dir", aeron,
                "--recording-id", recording_id, "--expected-count", "6",
                "--timeout-seconds", "10", "--summary-file", str(tmp_path / (label + ".summary"))])
            assert child.wait(timeout=15) == 0
            assert "status=SUCCESS" in (tmp_path / (label + ".summary")).read_text()
        assert (tmp_path / "compute.summary").read_bytes() == (tmp_path / "replay.summary").read_bytes()
        reorder_log = (tmp_path / ("mux.log" if through_mux else "publisher.log")).read_text()
        assert "recovered_gaps=2" in reorder_log
    finally:
        for child in reversed(processes):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        for handle in files:
            handle.close()
