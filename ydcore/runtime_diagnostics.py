"""Bounded, credential-free process diagnostics; no trading/retry decisions."""
import faulthandler
import json
import os
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timezone


class RuntimeDiagnostics:
    def __init__(self, interval=60):
        self.interval = interval
        self.phase = "starting"
        self.started = time.monotonic()
        self.progress = self.started
        self.done = threading.Event()
        self.signals = {}

    def emit(self, event, **fields):
        record = dict(event=event, utc=datetime.now(timezone.utc).isoformat(),
                      pid=os.getpid(), phase=self.phase,
                      uptime_seconds=round(time.monotonic() - self.started, 3), **fields)
        print("TRADER_DIAGNOSTIC " + json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)

    def error(self, event, error, tb=None):
        # Never serialize exception messages, locals, account configs or orders.
        frames = traceback.extract_tb(tb if tb is not None else error.__traceback__)
        self.emit(event, error_type=type(error).__name__,
                  stack=[dict(file=os.path.basename(f.filename), line=f.lineno, function=f.name)
                         for f in frames[-20:]])

    def tick(self):
        self.progress = time.monotonic()

    def _heartbeat(self):
        while not self.done.wait(self.interval):
            self.emit("heartbeat", main_loop_progress_age_seconds=round(time.monotonic() - self.progress, 3))

    def _signal(self, number, frame):
        self.emit("signal", signal=signal.Signals(number).name)
        raise SystemExit(128 + number)

    def __enter__(self):
        self.fault_enabled = faulthandler.is_enabled()
        try:
            faulthandler.enable(file=sys.stderr, all_threads=True)
        except (OSError, ValueError, RuntimeError):
            self.emit("fault_handler_unavailable")
        self.old_thread_hook = threading.excepthook
        def thread_hook(args):
            self.error("thread_exception", args.exc_value, args.exc_traceback)
        threading.excepthook = thread_hook
        if threading.current_thread() is threading.main_thread():
            for number in (signal.SIGTERM, signal.SIGINT):
                self.signals[number] = signal.getsignal(number)
                signal.signal(number, self._signal)
        self.emit("start")
        self.thread = threading.Thread(target=self._heartbeat, name="trader-heartbeat", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, kind, error, tb):
        self.done.set()
        self.thread.join(timeout=2)
        for number, previous in self.signals.items():
            signal.signal(number, previous)
        threading.excepthook = self.old_thread_hook
        if not self.fault_enabled:
            faulthandler.disable()
        code = 0
        if error is not None:
            code = error.code if isinstance(error, SystemExit) and isinstance(error.code, int) else 1
            self.error("exception", error, tb)
        self.emit("exit", exit_code=code)
        return False
