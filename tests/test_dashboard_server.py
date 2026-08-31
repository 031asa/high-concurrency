import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from dashboard.server import DashboardHandler, collect_status


class DashboardStatusTests(unittest.TestCase):
    def test_empty_result_root_is_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            status = collect_status(Path(directory))
        self.assertEqual("IDLE", status["dashboard_status"])
        self.assertIsNone(status["run"])

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
        self.assertEqual(2.0, status["compute"]["p95_ms"])
        self.assertEqual("IF2609", status["compute"]["quote"]["instrument"])
        self.assertEqual(
            "2026-01-02T09:30:00+08:00",
            status["compute"]["latency_by_time"][0]["time_bin"],
        )
        self.assertEqual("IF2609", status["compute"]["latency_by_contract"][0]["contract"])
        self.assertEqual(2.8, status["compute"]["latency_by_contract"][0]["p99_ms"])
        self.assertEqual(100, status["audit"]["received"])
        self.assertEqual([10, 100], [point["received"] for point in status["series"]])

    def test_http_health_and_status_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            DashboardHandler.result_root = Path(directory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(base_url + "/healthz", timeout=2) as response:
                    health = json.load(response)
                with urllib.request.urlopen(base_url + "/api/status", timeout=2) as response:
                    status = json.load(response)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        self.assertEqual("ok", health["status"])
        self.assertEqual("IDLE", status["dashboard_status"])


if __name__ == "__main__":
    unittest.main()
