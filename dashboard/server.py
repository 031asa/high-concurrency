#!/usr/bin/env python3
"""Small, dependency-free HTTP dashboard for Aeron MVP result streams."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse
from scripts.time_probe import history as collect_clock_history


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULT_ROOT = PROJECT_ROOT / "result" / "aeron-mvp"
DEFAULT_TIME_REPORT_ROOT = PROJECT_ROOT
INDEX_FILE = Path(__file__).resolve().with_name("index.html")
MAX_SERIES_POINTS = 720
CHINA_TZ = timezone(timedelta(hours=8))
MAX_PROGRESS_BYTES = 2 * 1024 * 1024
MAX_TIME_REPORT_BYTES = 256 * 1024
DEFAULT_TIME_REPORT_MAX_AGE_SECONDS = 300
MAX_REPORT_SAMPLE_SEPARATION_SECONDS = 60


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


def _read_json_object(path: Path) -> tuple[Optional[Dict[str, Any]], str]:
    try:
        if path.stat().st_size > MAX_TIME_REPORT_BYTES:
            return None, "报告文件过大"
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, ""
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return None, f"无法读取报告：{error}"
    if not isinstance(value, dict):
        return None, "报告根节点必须是对象"
    return value, ""


def _timestamp_ms(value: object) -> Optional[int]:
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError, OverflowError):
        return None


def _number(value: object) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _time_report(
    report_root: Path,
    platform: str,
    now_ms: int,
    max_age_seconds: int,
) -> Dict[str, Any]:
    path = report_root / f"{platform}-time.json"
    raw, read_error = _read_json_object(path)
    if raw is None:
        return {
            "platform": platform,
            "available": bool(read_error),
            "status": "INVALID" if read_error else "MISSING",
            "path": str(path),
            "failure": read_error,
        }

    failures: List[str] = []
    if raw.get("schema") != 1:
        failures.append("不支持的报告schema")
    if raw.get("platform") != platform:
        failures.append("报告平台不匹配")
    generated_ms = _timestamp_ms(raw.get("generated_at_utc"))
    if generated_ms is None:
        failures.append("报告时间无效")
        age_seconds = None
        fresh = False
    else:
        age_seconds = (now_ms - generated_ms) / 1000.0
        fresh = abs(age_seconds) <= max_age_seconds
        if not fresh:
            failures.append(f"报告超过{max_age_seconds}秒有效期")

    report_pass = raw.get("pass") is True
    if not report_pass:
        failures.append(str(raw.get("failure") or "报告自身未通过"))
    authority = raw.get("authority") if isinstance(raw.get("authority"), dict) else {}
    offset_ms = _number(raw.get("authority_minus_local_ms"))
    if offset_ms is None:
        failures.append("报告缺少有效offset")
    uncertainty_ms = _number(raw.get("uncertainty_ms"))
    stale_failure = f"报告超过{max_age_seconds}秒有效期"
    status = "PASS" if not failures else ("STALE" if failures == [stale_failure] else "FAIL")
    return {
        "platform": platform,
        "available": True,
        "status": status,
        "path": str(path),
        "hostname": raw.get("hostname", ""),
        "generated_at_utc": raw.get("generated_at_utc", ""),
        "generated_at_ms": generated_ms,
        "age_seconds": age_seconds,
        "fresh": fresh,
        "authority": {
            "name": authority.get("name", ""),
            "url": authority.get("url", ""),
            "ntp_servers": authority.get("ntp_servers", []),
            "environment": authority.get("environment", ""),
        },
        "selected_source": raw.get("selected_source", ""),
        "sync_mode": raw.get("sync_mode", "network_ntp"),
        "independent_authority_sync": raw.get("independent_authority_sync", platform == "windows"),
        "offset_ms": offset_ms,
        "uncertainty_ms": uncertainty_ms,
        "max_offset_ms": _number(raw.get("max_offset_ms")),
        "max_cross_difference_ms": _number(raw.get("max_cross_difference_ms")),
        "report_pass": report_pass,
        "failure": "; ".join(item for item in failures if item),
    }


def _authority_identity(report: Dict[str, Any]) -> tuple[object, ...]:
    authority = report.get("authority") or {}
    servers = authority.get("ntp_servers") or []
    return (
        authority.get("name"),
        authority.get("url"),
        tuple(sorted(str(item) for item in servers)),
        authority.get("environment"),
    )


def _compare_time_reports(windows: Dict[str, Any], linux: Dict[str, Any]) -> Dict[str, Any]:
    if windows.get("status") in {"MISSING", "INVALID"} or linux.get("status") in {"MISSING", "INVALID"}:
        return {"available": False, "status": "UNAVAILABLE", "failures": ["缺少有效的双端报告"]}

    failures: List[str] = []
    authority_match = _authority_identity(windows) == _authority_identity(linux)
    if not authority_match:
        failures.append("Windows与Linux授时源配置不一致")
    windows_generated = windows.get("generated_at_ms")
    linux_generated = linux.get("generated_at_ms")
    sample_gap_seconds = None
    if windows_generated is not None and linux_generated is not None:
        sample_gap_seconds = abs(windows_generated - linux_generated) / 1000.0
        if sample_gap_seconds > MAX_REPORT_SAMPLE_SEPARATION_SECONDS:
            failures.append("双端报告采样时间相差超过60秒")
    else:
        failures.append("双端报告时间无效")

    windows_offset = windows.get("offset_ms")
    linux_offset = linux.get("offset_ms")
    cross_difference_ms = None
    if windows_offset is not None and linux_offset is not None:
        cross_difference_ms = abs(float(windows_offset) - float(linux_offset))
    limits = [
        value for value in (windows.get("max_cross_difference_ms"), linux.get("max_cross_difference_ms"))
        if value is not None
    ]
    limit_ms = min(limits) if limits else None
    if cross_difference_ms is None or limit_ms is None:
        failures.append("缺少双端偏差或门槛")
    elif cross_difference_ms > limit_ms:
        failures.append(f"双端差值超过{limit_ms:g}ms")
    if windows.get("status") != "PASS":
        failures.append("Windows检测未通过")
    if linux.get("status") != "PASS":
        failures.append("Linux检测未通过")
    return {
        "available": True,
        "status": "PASS" if not failures else "FAIL",
        "authority_match": authority_match,
        "sample_gap_seconds": sample_gap_seconds,
        "cross_difference_ms": cross_difference_ms,
        "limit_ms": limit_ms,
        "failures": failures,
    }


def collect_time_sync_status(
    report_root: Path,
    now_ms: Optional[int] = None,
    max_age_seconds: int = DEFAULT_TIME_REPORT_MAX_AGE_SECONDS,
) -> Dict[str, Any]:
    generated_ms = int(time.time() * 1000) if now_ms is None else now_ms
    windows = _time_report(report_root, "windows", generated_ms, max_age_seconds)
    linux = _time_report(report_root, "linux", generated_ms, max_age_seconds)
    comparison = _compare_time_reports(windows, linux)
    if comparison.get("available"):
        detection_status = comparison["status"]
    elif windows.get("available") or linux.get("available"):
        detection_status = "FAIL" if "INVALID" in {windows.get("status"), linux.get("status")} else "PARTIAL"
    else:
        detection_status = "UNAVAILABLE"
    return {
        "generated_at_ms": generated_ms,
        "operation": {
            "execution": "external_privileged",
            "dashboard_adjusts_clock": False,
            "message": "对时操作由受控脚本执行；Dashboard只展示操作入口，不直接修改系统时间。",
            "windows": {
                "title": "Windows宿主对时",
                "requires_admin": True,
                "command": "powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\windows_time_sync.ps1 -Config .\\config\\time_authority.cffex.conf -Apply -Output .\\windows-time.json -NtpRoot D:\\NTP",
            },
            "linux": {
                "title": "Leader原生Linux对时",
                "requires_admin": True,
                "command": "sudo ./scripts/setup_linux_time_sync.sh --config ./config/time_authority.cffex.conf",
            },
        },
        "detection": {
            "status": detection_status,
            "max_report_age_seconds": max_age_seconds,
            "windows": windows,
            "linux": linux,
            "comparison": comparison,
            "message": "授时检测只使用NTP/PTP报告；交易所行情不参与对时判定。",
        },
    }


def _run_directories(result_root: Path) -> List[Path]:
    try:
        candidates = [entry for entry in result_root.iterdir() if entry.is_dir()]
    except OSError:
        return []
    return sorted(candidates, key=lambda entry: entry.name, reverse=True)


def _run_sources(meta: Dict[str, str]) -> List[str]:
    sources = [item for item in meta.get("sources", "").split(",") if item]
    if sources:
        return sources
    source = meta.get("source", "")
    return [source] if source and source not in {"multi", "unknown"} else []


def _source_catalog(meta: Dict[str, str]) -> List[Dict[str, Any]]:
    catalog: List[Dict[str, Any]] = []
    for source in _run_sources(meta):
        catalog.append(
            {
                "value": source,
                "label": source.upper(),
                "kind": meta.get(f"source_kind.{source}", ""),
                "latency_mode": meta.get(f"source_latency_mode.{source}", ""),
            }
        )
    return catalog


def _latest_run(run_dirs: Iterable[Path]) -> Optional[Path]:
    for run_dir in run_dirs:
        return run_dir
    return None


def _source_view(payload: Dict[str, Any], source: Optional[str]) -> Optional[Dict[str, Any]]:
    if not source:
        return None
    views = payload.get("source_views")
    if not isinstance(views, list):
        return None
    for view in views:
        if isinstance(view, dict) and view.get("source") == source:
            return view
    return None


def _source_latency(payload: Dict[str, Any], source: Optional[str]) -> Dict[str, Any]:
    view = _source_view(payload, source)
    if view is None:
        return {}
    latency = view.get("latency")
    return latency if isinstance(latency, dict) else {}


def _consumer_payload(run_dir: Path, name: str) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    rows = _read_progress(run_dir / f"{name}-live.ndjson")
    current: Dict[str, Any] = dict(rows[-1]) if rows else {}
    retained_fields = (
        "quote",
        "latency_by_time",
        "latency_by_contract",
        "latency_by_source",
        "source_views",
    )
    for row in reversed(rows):
        for field in retained_fields:
            if field not in current and field in row:
                current[field] = row[field]
        if all(field in current for field in retained_fields):
            break
    final = _summary(run_dir / f"{name}-live.summary")
    if final:
        current.update(final)
        current["finished"] = True
    return current, rows


def _select_compute_source(
    compute: Dict[str, Any], selected_source: Optional[str], isolate_source: bool
) -> Dict[str, Any]:
    selected = dict(compute)
    view = _source_view(compute, selected_source)
    if view is not None:
        latency = view.get("latency") if isinstance(view.get("latency"), dict) else {}
        selected["quote"] = view.get("quote") if isinstance(view.get("quote"), dict) else {}
        selected["latency_by_time"] = (
            view.get("latency_by_time") if isinstance(view.get("latency_by_time"), list) else []
        )
        selected["latency_by_contract"] = (
            view.get("latency_by_contract")
            if isinstance(view.get("latency_by_contract"), list)
            else []
        )
        for field in ("mean_ms", "std_ms", "p50_ms", "p90_ms", "p95_ms", "p99_ms", "max_ms"):
            selected[field] = latency.get(field)
        selected["source_measured"] = latency.get("count", 0)
    elif isolate_source:
        selected["quote"] = {}
        selected["latency_by_time"] = []
        selected["latency_by_contract"] = []
        for field in ("mean_ms", "std_ms", "p50_ms", "p90_ms", "p95_ms", "p99_ms", "max_ms"):
            selected[field] = None
        selected["source_measured"] = 0
    selected["selected_source"] = selected_source
    return selected


def _daily_view(payload: Dict[str, Any], source: Optional[str], today: str) -> Dict[str, Any]:
    view = _source_view(payload, source) or {}
    daily = view.get("daily_latency")
    provided = isinstance(daily, dict)
    current = provided and daily.get("date") == today
    latency = daily.get("latency") if current else None
    latency = latency if isinstance(latency, dict) and latency.get("count", 0) > 0 else {}
    legacy = bool(view) or ("source_views" not in payload and
                            bool(payload.get("quote") or payload.get("received")))
    status = "READY" if latency else ("NOT_PROVIDED" if not provided and legacy else "WAITING")
    return {
        "date": today,
        "status": status,
        "message": {"READY": "北京时间当天统计", "WAITING": "等待当天行情",
                    "NOT_PROVIDED": "该运行未提供当天统计"}[status],
        "latency": latency,
        "latency_by_time": daily.get("latency_by_time", []) if latency else [],
        "latency_by_contract": daily.get("latency_by_contract", []) if latency else [],
        "excluded": {key: daily.get(key, 0) if current else 0 for key in
                     ("old_snapshots", "future_dates", "invalid_timestamps")},
    }


def collect_status(result_root: Path, source: Optional[str] = None) -> Dict[str, Any]:
    now_ms = int(time.time() * 1000)
    requested_source = source or None
    run_dirs = _run_directories(result_root)
    run_dir = _latest_run(run_dirs)
    if run_dir is None:
        return {
            "dashboard_status": "IDLE",
            "generated_at_ms": now_ms,
            "run": None,
            "compute": {},
            "audit": {},
            "market_observation": {},
            "series": [],
            "source_selection": {
                "requested": requested_source,
                "selected": None,
                "available": [],
            },
        }

    meta = _parse_key_values(run_dir / "run.meta")
    sources = _run_sources(meta)
    available_sources = _source_catalog(meta)
    if requested_source is None:
        selected_source = sources[0] if sources else None
    else:
        selected_source = requested_source if requested_source in sources else None
    source_kind = meta.get(f"source_kind.{selected_source}", "") if selected_source else ""
    source_latency_mode = (
        meta.get(f"source_latency_mode.{selected_source}", "") if selected_source else ""
    )
    if len(sources) == 1:
        source_kind = source_kind or meta.get("source", "")
        source_latency_mode = source_latency_mode or meta.get("latency_mode", "live")
    raw_compute, compute_rows = _consumer_payload(run_dir, "compute")
    isolate_source = meta.get("source") == "multi" or len(sources) > 1
    compute = _select_compute_source(raw_compute, selected_source, isolate_source)
    session_latency = dict(_source_latency(raw_compute, selected_source))
    if not session_latency and not isolate_source and selected_source:
        session_latency = {key: raw_compute.get(key) for key in
                           ("mean_ms", "std_ms", "p50_ms", "p90_ms", "p95_ms", "p99_ms", "max_ms")}
        session_latency["count"] = raw_compute.get("measured", 0)
    compute["session_latency"] = session_latency
    today = datetime.fromtimestamp(now_ms / 1000, CHINA_TZ).date().isoformat()
    daily = _daily_view(raw_compute, selected_source, today)
    for field in ("mean_ms", "std_ms", "p50_ms", "p90_ms", "p95_ms", "p99_ms", "max_ms"):
        compute[field] = daily["latency"].get(field)
    compute["source_measured"] = daily["latency"].get("count", 0)
    compute["latency_by_time"] = daily["latency_by_time"]
    compute["latency_by_contract"] = daily["latency_by_contract"]
    compute["daily_latency"] = daily
    audit, _ = _consumer_payload(run_dir, "audit")
    run_status = meta.get("status", "RUNNING")
    run = {
        "id": meta.get("run_id", run_dir.name),
        "source": meta.get("source", "unknown"),
        "sources": sources,
        "selected_source": selected_source,
        "selected_source_kind": source_kind,
        "selected_source_latency_mode": source_latency_mode,
        "latency_mode": meta.get("latency_mode", "live"),
        "expected_count": int(meta.get("expected_count", compute.get("expected", 0)) or 0),
        "sync_level": int(meta.get("sync_level", 0) or 0),
        "started_at_utc": meta.get("started_at_utc", ""),
        "recording_id": meta.get("recording_id", compute.get("recording_id", "")),
        "status": run_status,
        "result_dir": str(run_dir),
    }
    series = [
        {
            **{
                key: row.get(key)
                for key in (
                    "timestamp_ms",
                    "received",
                    "rate_per_second",
                )
            },
            **(_daily_view(row, selected_source, today)["latency"]
               if datetime.fromtimestamp((row.get("timestamp_ms") or 0) / 1000, CHINA_TZ)
                   .date().isoformat() == today else {}),
        }
        for row in compute_rows
    ]
    for point in series:
        point["observed_delta_ms"] = point.get("mean_ms")
    historical = source_latency_mode == "historical_replay"
    if historical:
        compute["latency_by_time"] = []
        compute["latency_by_contract"] = []
        for point in series:
            point["observed_delta_ms"] = None
    market_observation = {
        "status": "HISTORICAL" if historical else ("SYNTHETIC" if source_kind == "synthetic" else "LIVE"),
        "valid_for_live_observation": not historical,
        "metric": "absolute_timestamp_delta",
        "definition": "|本地回调接收时间 - 行情事件时间|；不等于授时偏差，不参与对时。",
        "mean_abs_ms": None if historical else compute.get("mean_ms"),
        "p95_abs_ms": None if historical else compute.get("p95_ms"),
        "max_abs_ms": None if historical else compute.get("max_ms"),
        "statistics_date": today,
        "timezone": "Asia/Shanghai",
        "daily_status": daily["status"],
        "daily_message": daily["message"],
        "sample_count": daily["latency"].get("count", 0),
        "session_mean_abs_ms": None if historical else session_latency.get("mean_ms"),
        "session_sample_count": session_latency.get("count", 0),
        "excluded": daily["excluded"],
        "last_market_time": (compute.get("quote") or {}).get("market_time"),
    }
    return {
        "dashboard_status": run_status,
        "generated_at_ms": now_ms,
        "run": run,
        "compute": compute,
        "audit": audit,
        "market_observation": market_observation,
        "series": series,
        "source_selection": {
            "requested": requested_source,
            "selected": selected_source,
            "available": available_sources,
        },
    }


class DashboardHandler(BaseHTTPRequestHandler):
    clock_history_root = PROJECT_ROOT / "result" / "time-probes"
    result_root = DEFAULT_RESULT_ROOT
    time_report_root = DEFAULT_TIME_REPORT_ROOT
    time_report_max_age_seconds = DEFAULT_TIME_REPORT_MAX_AGE_SECONDS
    index_file = INDEX_FILE

    def do_GET(self) -> None:  # noqa: N802 - stdlib API name
        request = urlparse(self.path)
        path = request.path
        if path in {"/", "/index.html"}:
            self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", self.index_file.read_bytes())
            return
        if path == "/api/status":
            source = parse_qs(request.query).get("source", [None])[0]
            self._send_json(HTTPStatus.OK, collect_status(self.result_root, source=source))
            return
        if path == "/api/time-sync":
            self._send_json(
                HTTPStatus.OK,
                collect_time_sync_status(self.time_report_root, max_age_seconds=self.time_report_max_age_seconds),
            )
            return
        if path == "/api/clock-history":
            day = parse_qs(request.query).get("date", [None])[0]
            try:
                payload = collect_clock_history(self.clock_history_root, day)
            except ValueError as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            self._send_json(HTTPStatus.OK, payload)
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
    parser.add_argument("--time-report-root", type=Path, default=DEFAULT_TIME_REPORT_ROOT)
    parser.add_argument("--clock-history-root", type=Path, default=PROJECT_ROOT / "result/time-probes")
    parser.add_argument(
        "--time-report-max-age-seconds",
        type=int,
        default=DEFAULT_TIME_REPORT_MAX_AGE_SECONDS,
    )
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    DashboardHandler.result_root = args.result_root.resolve()
    DashboardHandler.time_report_root = args.time_report_root.resolve()
    DashboardHandler.clock_history_root = args.clock_history_root.resolve()
    DashboardHandler.time_report_max_age_seconds = max(1, args.time_report_max_age_seconds)
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
