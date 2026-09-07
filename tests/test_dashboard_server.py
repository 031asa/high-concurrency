import json
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from pathlib import Path

from dashboard.server import DashboardHandler, collect_status, collect_time_sync_status


NOW_MS = 1767333600000  # 2026-01-02T06:00:00Z


def time_report(platform, offset_ms, generated_at="2026-01-02T06:00:00.000Z"):
    return {
        "schema": 1,
        "platform": platform,
        "hostname": f"{platform}-host",
        "generated_at_utc": generated_at,
        "authority": {
            "name": "Approved Exchange Time",
            "url": "https://time.example.test/",
            "ntp_servers": ["192.0.2.10"],
            "environment": "production",
        },
        "selected_source": "192.0.2.10",
        "authority_minus_local_ms": offset_ms,
        "uncertainty_ms": 0.5,
        "max_abs_sample_ms": abs(offset_ms),
        "max_offset_ms": 5,
        "max_cross_difference_ms": 5,
        "pass": True,
        "failure": "",
    }


class DashboardStatusTests(unittest.TestCase):
    def test_empty_result_root_is_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            status = collect_status(Path(directory))
        self.assertEqual("IDLE", status["dashboard_status"])
        self.assertIsNone(status["run"])
        self.assertEqual([], status["source_selection"]["available"])

    def test_status_uses_latest_run_sources_in_bash_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "20260102T000000Z-2"
            old.mkdir()
            (old / "run.meta").write_text(
                "run_id=old\nsource=old-source\nstatus=SUCCESS\n",
                encoding="utf-8",
            )
            run = root / "20260103T000000Z-3"
            run.mkdir()
            (run / "run.meta").write_text(
                "run_id=current\nsource=multi\nsources=ydapi-main,ctp-live-main\n"
                "source_kind.ydapi-main=ydapi\nsource_latency_mode.ydapi-main=live\n"
                "source_kind.ctp-live-main=ctp\n"
                "source_latency_mode.ctp-live-main=historical_replay\n"
                "latency_mode=mixed\nstatus=RUNNING\n",
                encoding="utf-8",
            )

            automatic = collect_status(root)
            selected = collect_status(root, source="ctp-live-main")
            missing = collect_status(root, source="old-source")

        self.assertEqual("current", automatic["run"]["id"])
        self.assertEqual("ydapi-main", automatic["source_selection"]["selected"])
        self.assertEqual(
            ["ydapi-main", "ctp-live-main"],
            [item["value"] for item in selected["source_selection"]["available"]],
        )
        self.assertEqual("ctp-live-main", selected["source_selection"]["selected"])
        self.assertEqual("historical_replay", selected["run"]["selected_source_latency_mode"])
        self.assertFalse(selected["market_observation"]["valid_for_live_observation"])
        self.assertIsNone(missing["source_selection"]["selected"])

    def test_source_selection_filters_quote_and_latency_without_cross_source_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "20260104T000000Z-4"
            run.mkdir()
            (run / "run.meta").write_text(
                "run_id=multi-run\nsource=multi\nsources=ydapi-main,ctp-live-main\n"
                "source_kind.ydapi-main=ydapi\nsource_latency_mode.ydapi-main=live\n"
                "source_kind.ctp-live-main=ctp\nsource_latency_mode.ctp-live-main=live\n"
                "latency_mode=live\nexpected_count=20\nstatus=RUNNING\n",
                encoding="utf-8",
            )
            progress = {
                "timestamp_ms": 2,
                "received": 20,
                "rate_per_second": 50.0,
                "quote": {"source": "ctp-live-main", "instrument": "IF2609", "last_price": 4200.0},
                "mean_ms": 99.0,
                "source_views": [
                    {
                        "source": "ydapi-main",
                        "quote": {"source": "ydapi-main", "instrument": "IC2609", "last_price": 5100.5},
                        "latency": {"source": "ydapi-main", "count": 9, "mean_ms": 1.25,
                                    "std_ms": 0.2, "p50_ms": 1.1, "p90_ms": 1.5,
                                    "p95_ms": 1.7, "p99_ms": 1.9, "max_ms": 2.0},
                        "latency_by_time": [{"time_bin": "ydapi-bin", "count": 9}],
                        "latency_by_contract": [{"contract": "IC2609", "count": 9}],
                    },
                    {
                        "source": "ctp-live-main",
                        "quote": {"source": "ctp-live-main", "instrument": "IF2609", "last_price": 4200.0},
                        "latency": {"source": "ctp-live-main", "count": 8, "mean_ms": 3.5,
                                    "std_ms": 0.4, "p50_ms": 3.1, "p90_ms": 3.8,
                                    "p95_ms": 4.0, "p99_ms": 4.2, "max_ms": 4.5},
                        "latency_by_time": [{"time_bin": "ctp-bin", "count": 8}],
                        "latency_by_contract": [{"contract": "IF2609", "count": 8}],
                    },
                ],
            }
            progress["timestamp_ms"] = NOW_MS
            for view in progress["source_views"]:
                view["daily_latency"] = {
                    "date": "2026-01-02", "latency": dict(view["latency"]),
                    "latency_by_time": view["latency_by_time"],
                    "latency_by_contract": view["latency_by_contract"],
                    "old_snapshots": 2, "future_dates": 1, "invalid_timestamps": 3,
                }
                view["latency"] = {"mean_ms": 999999, "count": 100}
            (run / "compute-live.ndjson").write_text(
                json.dumps(progress) + "\n", encoding="utf-8"
            )

            with patch("dashboard.server.time.time", return_value=NOW_MS / 1000):
                ydapi = collect_status(root, source="ydapi-main")
                ctp = collect_status(root, source="ctp-live-main")
            with patch("dashboard.server.time.time", return_value=NOW_MS / 1000 + 86400):
                tomorrow = collect_status(root, source="ydapi-main")
            self.assertIsNone(tomorrow["market_observation"]["mean_abs_ms"])
            self.assertEqual("WAITING", tomorrow["market_observation"]["daily_status"])
            self.assertEqual([], tomorrow["compute"]["latency_by_contract"])
            self.assertIsNone(tomorrow["series"][0]["observed_delta_ms"])
            self.assertEqual(0, tomorrow["market_observation"]["excluded"]["old_snapshots"])

        self.assertEqual("IC2609", ydapi["compute"]["quote"]["instrument"])
        self.assertEqual(5100.5, ydapi["compute"]["quote"]["last_price"])
        self.assertEqual(1.25, ydapi["compute"]["mean_ms"])
        self.assertEqual("ydapi-bin", ydapi["compute"]["latency_by_time"][0]["time_bin"])
        self.assertEqual("IC2609", ydapi["compute"]["latency_by_contract"][0]["contract"])
        self.assertEqual(1.25, ydapi["series"][0]["observed_delta_ms"])
        self.assertEqual("IF2609", ctp["compute"]["quote"]["instrument"])
        self.assertEqual(4200.0, ctp["compute"]["quote"]["last_price"])
        self.assertEqual(3.5, ctp["market_observation"]["mean_abs_ms"])
        self.assertEqual(20, ctp["compute"]["received"])
        self.assertEqual(2, ctp["market_observation"]["excluded"]["old_snapshots"])
        self.assertEqual(1, ctp["market_observation"]["excluded"]["future_dates"])
        self.assertEqual(3, ctp["market_observation"]["excluded"]["invalid_timestamps"])
        self.assertEqual(8, ctp["market_observation"]["sample_count"])
        self.assertEqual(999999, ctp["market_observation"]["session_mean_abs_ms"])
        self.assertEqual(100, ctp["market_observation"]["session_sample_count"])

    def test_configured_source_without_quote_does_not_fallback_to_another_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "20260104T000000Z-4"
            run.mkdir()
            (run / "run.meta").write_text(
                "run_id=multi-run\nsource=multi\nsources=ydapi-main,ctp-live-main\n"
                "source_kind.ydapi-main=ydapi\nsource_kind.ctp-live-main=ctp\n"
                "source_latency_mode.ydapi-main=live\nsource_latency_mode.ctp-live-main=live\n"
                "status=RUNNING\n",
                encoding="utf-8",
            )
            progress = {
                "quote": {"source": "ydapi-main", "last_price": 5100.5},
                "mean_ms": 1.25,
                "source_views": [{
                    "source": "ydapi-main",
                    "quote": {"source": "ydapi-main", "last_price": 5100.5},
                    "latency": {"source": "ydapi-main", "count": 1, "mean_ms": 1.25},
                    "latency_by_time": [],
                    "latency_by_contract": [],
                }],
            }
            (run / "compute-live.ndjson").write_text(
                json.dumps(progress) + "\n", encoding="utf-8"
            )

            status = collect_status(root, source="ctp-live-main")

        self.assertEqual({}, status["compute"]["quote"])
        self.assertIsNone(status["compute"]["mean_ms"])
        self.assertEqual([], status["compute"]["latency_by_time"])
        self.assertEqual("ctp-live-main", status["source_selection"]["selected"])

    def test_source_with_quote_but_no_latency_sample_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "20260104T000000Z-5"
            run.mkdir()
            (run / "run.meta").write_text(
                "run_id=multi-run\nsource=multi\nsources=ydapi-main,ctp-live-main\n"
                "source_kind.ydapi-main=ydapi\nsource_kind.ctp-live-main=ctp\n"
                "source_latency_mode.ydapi-main=live\n"
                "source_latency_mode.ctp-live-main=live\nstatus=RUNNING\n",
                encoding="utf-8",
            )
            progress = {
                "timestamp_ms": 1,
                "received": 1,
                "source_views": [{
                    "source": "ydapi-main",
                    "quote": {"source": "ydapi-main", "last_price": 5100.5},
                    "latency": None,
                    "latency_by_time": [],
                    "latency_by_contract": [],
                }],
            }
            (run / "compute-live.ndjson").write_text(
                json.dumps(progress) + "\n", encoding="utf-8"
            )

            status = collect_status(root, source="ydapi-main")

        self.assertEqual(5100.5, status["compute"]["quote"]["last_price"])
        self.assertIsNone(status["series"][0]["observed_delta_ms"])
        self.assertEqual(0, status["compute"]["source_measured"])

    def test_latest_run_merges_progress_and_final_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "20260101T000000Z-1"
            old.mkdir()
            run = root / "20260102T000000Z-2"
            run.mkdir()
            (run / "run.meta").write_text(
                "run_id=latest\nsource=synthetic\nlatency_mode=live\nexpected_count=100\n"
                "sync_level=0\nstarted_at_utc=2026-01-02T00:00:00Z\n"
                "status=SUCCESS\nrecording_id=42\n",
                encoding="utf-8",
            )
            rows = [
                {"timestamp_ms": 1, "status": "RUNNING", "received": 10, "rate_per_second": 20.0,
                 "mean_ms": 1.25,
                 "quote": {"instrument": "IF2609", "last_price": 4012.4},
                 "latency_by_time": [{
                     "time_bin": "2026-01-02T09:30:00+08:00", "count": 9,
                     "mean_ms": 1.25, "std_ms": 0.5, "p50_ms": 1.0,
                     "p90_ms": 1.8, "p95_ms": 2.0, "p99_ms": 2.8, "max_ms": 3.0,
                 }],
                 "latency_by_contract": [{
                     "contract": "IF2609", "count": 9,
                     "mean_ms": 1.25, "std_ms": 0.5, "p50_ms": 1.0,
                     "p90_ms": 1.8, "p95_ms": 2.0, "p99_ms": 2.8, "max_ms": 3.0,
                 }]},
                {"timestamp_ms": 2, "status": "RUNNING", "received": 100, "rate_per_second": 50.0},
            ]
            (run / "compute-live.ndjson").write_text(
                "bad-json\n" + "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            (run / "compute-live.summary").write_text(
                "mode=COMPUTE\nstatus=SUCCESS\nreceived=100\nmeasured=99\n"
                "gaps=0\nduplicates=0\ninvalid_timestamps=0\n"
                "mean_ms=1.25\nstd_ms=0.5\np95_ms=2.0\nmax_ms=3.0\n",
                encoding="utf-8",
            )
            (run / "audit-live.summary").write_text(
                "mode=AUDIT\nstatus=SUCCESS\nreceived=100\ngaps=0\n"
                "duplicates=0\ninvalid_timestamps=0\n",
                encoding="utf-8",
            )

            status = collect_status(root)

        self.assertEqual("SUCCESS", status["dashboard_status"])
        self.assertEqual("latest", status["run"]["id"])
        self.assertEqual("live", status["run"]["latency_mode"])
        self.assertIsNone(status["compute"]["p95_ms"])
        self.assertEqual("IF2609", status["compute"]["quote"]["instrument"])
        self.assertEqual([], status["compute"]["latency_by_time"])
        self.assertEqual([], status["compute"]["latency_by_contract"])
        self.assertEqual("该运行未提供当天统计", status["market_observation"]["daily_message"])
        self.assertEqual(100, status["audit"]["received"])
        self.assertEqual([10, 100], [point["received"] for point in status["series"]])
        self.assertEqual("absolute_timestamp_delta", status["market_observation"]["metric"])
        self.assertIsNone(status["market_observation"]["mean_abs_ms"])
        self.assertEqual([None, None], [point["observed_delta_ms"] for point in status["series"]])
        self.assertEqual(1.25, status["market_observation"]["session_mean_abs_ms"])

    def test_daily_empty_exclusions_and_historical_hiding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            meta = "source=multi\nsources=live,tts\nsource_latency_mode.live=live\nsource_latency_mode.tts=historical_replay\n"
            (run / "run.meta").write_text(meta)
            daily = {"date": "2026-01-02", "latency": {"count": 0, "mean_ms": 0},
                     "old_snapshots": 5, "future_dates": 2, "invalid_timestamps": 1}
            payload = {"timestamp_ms": NOW_MS, "received": 8, "source_views": [
                {"source": "live", "quote": {"instrument": "A", "market_time": "20260101 23:00:00"},
                 "daily_latency": daily, "latency": {"mean_ms": 5000, "count": 8}},
                {"source": "tts", "daily_latency": {"date": "2026-01-02",
                 "latency": {"count": 1, "mean_ms": 80}, "latency_by_time": [{"count": 1}]},
                 "latency": {"mean_ms": 9000, "count": 1}}]}
            (run / "compute-live.ndjson").write_text(json.dumps(payload) + "\n")
            with patch("dashboard.server.time.time", return_value=NOW_MS / 1000):
                live = collect_status(root, "live")
                historical = collect_status(root, "tts")
            self.assertEqual("WAITING", live["market_observation"]["daily_status"])
            self.assertIsNone(live["market_observation"]["mean_abs_ms"])
            self.assertEqual(5, live["market_observation"]["excluded"]["old_snapshots"])
            self.assertEqual(5000, live["market_observation"]["session_mean_abs_ms"])
            self.assertIsNone(historical["market_observation"]["mean_abs_ms"])
            self.assertIsNone(historical["market_observation"]["session_mean_abs_ms"])
            self.assertIsNone(historical["series"][0]["observed_delta_ms"])
            self.assertEqual([], historical["compute"]["latency_by_time"])

    def test_time_sync_is_separate_and_unavailable_without_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            status = collect_time_sync_status(Path(directory), now_ms=NOW_MS)

        self.assertFalse(status["operation"]["dashboard_adjusts_clock"])
        self.assertEqual("external_privileged", status["operation"]["execution"])
        self.assertIn("-Apply", status["operation"]["windows"]["command"])
        self.assertEqual("UNAVAILABLE", status["detection"]["status"])
        self.assertEqual("MISSING", status["detection"]["windows"]["status"])
        self.assertEqual("MISSING", status["detection"]["linux"]["status"])

    def test_time_sync_detection_compares_fresh_authoritative_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "windows-time.json").write_text(
                json.dumps(time_report("windows", 1.25)), encoding="utf-8"
            )
            (root / "linux-time.json").write_text(
                json.dumps(time_report("linux", -0.75)), encoding="utf-8"
            )

            status = collect_time_sync_status(root, now_ms=NOW_MS)

        detection = status["detection"]
        self.assertEqual("PASS", detection["status"])
        self.assertEqual("PASS", detection["windows"]["status"])
        self.assertEqual("PASS", detection["linux"]["status"])
        self.assertEqual(2.0, detection["comparison"]["cross_difference_ms"])
        self.assertTrue(detection["comparison"]["authority_match"])

    def test_time_sync_detection_rejects_stale_or_mismatched_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            windows = time_report("windows", 1.0, "2026-01-02T05:00:00.000Z")
            linux = time_report("linux", 9.0, "2026-01-02T05:00:00.000Z")
            linux["authority"]["ntp_servers"] = ["192.0.2.11"]
            (root / "windows-time.json").write_text(json.dumps(windows), encoding="utf-8")
            (root / "linux-time.json").write_text(json.dumps(linux), encoding="utf-8")

            status = collect_time_sync_status(root, now_ms=NOW_MS)

        detection = status["detection"]
        self.assertEqual("FAIL", detection["status"])
        self.assertEqual("STALE", detection["windows"]["status"])
        self.assertFalse(detection["comparison"]["authority_match"])
        self.assertGreater(detection["comparison"]["cross_difference_ms"], 5)

    def test_http_health_and_status_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            DashboardHandler.result_root = Path(directory)
            DashboardHandler.time_report_root = Path(directory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(base_url + "/healthz", timeout=2) as response:
                    health = json.load(response)
                with urllib.request.urlopen(base_url + "/api/status?source=ydapi", timeout=2) as response:
                    status = json.load(response)
                with urllib.request.urlopen(base_url + "/api/time-sync", timeout=2) as response:
                    time_sync = json.load(response)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        self.assertEqual("ok", health["status"])
        self.assertEqual("IDLE", status["dashboard_status"])
        self.assertEqual("ydapi", status["source_selection"]["requested"])
        self.assertEqual("UNAVAILABLE", time_sync["detection"]["status"])


if __name__ == "__main__":
    unittest.main()
