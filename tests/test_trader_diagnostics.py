"""Offline subprocess checks: never construct a real SDK or Redis client."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHILD = r"""
import os, resource, sys, threading, time
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
from ydcore import yd_redis_server as module
if os.environ.get("YDTRADER_EXPECT_COMPILED") == "1":
    from ydcore import runtime_diagnostics
    assert module.__file__.endswith(".so"), module.__file__
    assert runtime_diagnostics.__file__.endswith(".so"), runtime_diagnostics.__file__
mode = sys.argv[1]
class FakeService:
    def __init__(self, **kwargs):
        if mode == "constructor":
            raise ValueError("SECRET_ACCOUNT_PASSWORD")
    def start(self, timeout):
        if mode == "startup":
            raise RuntimeError("SECRET_ACCOUNT_PASSWORD")
    def run_forever(self):
        if mode == "normal":
            return
        if mode == "systemexit":
            raise SystemExit(7)
        if mode == "thread":
            def fail():
                raise ValueError("SECRET_ACCOUNT_PASSWORD")
            thread = threading.Thread(target=fail)
            thread.start(); thread.join()
            return
        if mode == "fatal":
            os.abort()
        while True:
            time.sleep(.05)
    def stop(self):
        print("FAKE_STOP", flush=True)
module.YdRedisTraderService = FakeService
raise SystemExit(module.main(["--heartbeat-seconds", "1"]))
"""


def child(mode):
    return subprocess.Popen([sys.executable, "-u", "-c", CHILD, mode],
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def events(stderr):
    return [json.loads(line.split(" ", 1)[1]) for line in stderr.splitlines()
            if line.startswith("TRADER_DIAGNOSTIC ")]


@pytest.mark.parametrize("mode,code,event", [
    ("normal", 0, "exit"), ("constructor", 1, "exception"),
    ("startup", 1, "exception"), ("systemexit", 7, "exception"),
    ("thread", 0, "thread_exception"),
])
def test_lifecycle(mode, code, event):
    proc = child(mode)
    stdout, stderr = proc.communicate(timeout=15)
    assert proc.returncode == code
    records = events(stderr)
    assert event in [r["event"] for r in records]
    assert records[-1]["event"] == "exit" and records[-1]["exit_code"] == code
    assert "SECRET_ACCOUNT_PASSWORD" not in stdout + stderr
    if mode != "constructor":
        assert "FAKE_STOP" in stdout


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGINT])
def test_idle_heartbeat_and_signal(number):
    proc = child("idle")
    try:
        # Wait for a real ready record, rather than guessing SDK import time.
        lines = []
        import select
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if select.select([proc.stderr], [], [], .2)[0]:
                line = proc.stderr.readline()
                lines.append(line)
                if '"event": "heartbeat"' in line:
                    break
            assert proc.poll() is None
        else:
            pytest.fail("No heartbeat")
        proc.send_signal(number)
        stdout, stderr = proc.communicate(timeout=10)
        records = events("".join(lines) + stderr)
        assert proc.returncode == 128 + number
        assert any(r["event"] == "heartbeat" and r["main_loop_progress_age_seconds"] >= .5 for r in records)
        assert any(r["event"] == "signal" for r in records)
        assert records[-1]["exit_code"] == 128 + number
        assert "FAKE_STOP" in stdout
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def test_native_abort_has_fault_trace():
    proc = child("fatal")
    stdout, stderr = proc.communicate(timeout=15)
    assert proc.returncode == -signal.SIGABRT
    assert "Fatal Python error: Aborted" in stderr
    assert any(r["event"] == "ready" for r in events(stderr))
    # Native exit cannot run Python finally/atexit; do not promise an exit record.
    assert not any(r["event"] == "exit" for r in events(stderr))
