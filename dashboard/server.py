#!/usr/bin/env python3
"""Small, dependency-free HTTP dashboard for Aeron MVP result streams."""

from __future__ import annotations

import argparse
import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULT_ROOT = PROJECT_ROOT / "result" / "aeron-mvp"
INDEX_FILE = Path(__file__).resolve().with_name("index.html")
MAX_SERIES_POINTS = 720
MAX_PROGRESS_BYTES = 2 * 1024 * 1024


def _parse_key_values(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeError):
        return values
    for line in lines:
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _coerce(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _read_progress(path: Path) -> List[Dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, 2)
            start = max(0, size - MAX_PROGRESS_BYTES)
            handle.seek(start)
            if start:
                handle.readline()
            payload = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    rows: List[Dict[str, Any]] = []
    for line in payload.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows[-MAX_SERIES_POINTS:]


def _summary(path: Path) -> Dict[str, Any]:
    return {key: _coerce(value) for key, value in _parse_key_values(path).items()}


def _latest_run(result_root: Path) -> Optional[Path]:
    try:
        candidates = [entry for entry in result_root.iterdir() if entry.is_dir()]
    except OSError:
        return None
    return max(candidates, key=lambda entry: entry.name) if candidates else None


def _consumer_payload(run_dir: Path, name: str) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    rows = _read_progress(run_dir / f"{name}-live.ndjson")
    current: Dict[str, Any] = dict(rows[-1]) if rows else {}
    final = _summary(run_dir / f"{name}-live.summary")
    if final:
        current.update(final)
        current["finished"] = True
    return current, rows


def collect_status(result_root: Path) -> Dict[str, Any]:
    now_ms = int(time.time() * 1000)
    run_dir = _latest_run(result_root)
    if run_dir is None:
        return {
            "dashboard_status": "IDLE",
            "generated_at_ms": now_ms,
            "run": None,
            "compute": {},
            "audit": {},
            "series": [],
        }

    meta = _parse_key_values(run_dir / "run.meta")
    compute, compute_rows = _consumer_payload(run_dir, "compute")
    audit, _ = _consumer_payload(run_dir, "audit")
    run_status = meta.get("status", "RUNNING")
    run = {
        "id": meta.get("run_id", run_dir.name),
        "source": meta.get("source", "unknown"),
        "expected_count": int(meta.get("expected_count", compute.get("expected", 0)) or 0),
        "sync_level": int(meta.get("sync_level", 0) or 0),
        "started_at_utc": meta.get("started_at_utc", ""),
        "recording_id": meta.get("recording_id", compute.get("recording_id", "")),
        "status": run_status,
        "result_dir": str(run_dir),
    }
    series = [
        {
            key: row.get(key)
            for key in (
                "timestamp_ms",
                "received",
                "rate_per_second",
                "mean_ms",
                "p95_ms",
                "max_ms",
            )
        }
        for row in compute_rows
    ]
    return {
        "dashboard_status": run_status,
        "generated_at_ms": now_ms,
        "run": run,
        "compute": compute,
        "audit": audit,
        "series": series,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    result_root = DEFAULT_RESULT_ROOT
    index_file = INDEX_FILE

    def do_GET(self) -> None:  # noqa: N802 - stdlib API name
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", self.index_file.read_bytes())
            return
        if path == "/api/status":
            self._send_json(HTTPStatus.OK, collect_status(self.result_root))
            return
        if path == "/healthz":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def log_message(self, fmt: str, *args: object) -> None:
        print("DASHBOARD_HTTP " + (fmt % args), flush=True)

    def _send_json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def _send_bytes(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    DashboardHandler.result_root = args.result_root.resolve()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(
        f"YDTRADER_DASHBOARD url=http://{args.host}:{args.port} "
        f"result_root={DashboardHandler.result_root}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
