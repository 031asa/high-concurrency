import os
from pathlib import Path
import subprocess

SCRIPT = Path(__file__).parents[1] / "timer_pdf/code/run_daily_docker.sh"


def test_docker_daily_arguments_and_exit(tmp_path):
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 7\n')
    docker.chmod(0o755)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"],
               YDTRADER_REPORT_EXECUTABLE="/opt/ydtrader/ydtrader")
    p = subprocess.run(["bash", str(SCRIPT), "yd-market", "2026-09-09",
                        "--output-root", "/reports with spaces"], env=env, capture_output=True, text=True)
    assert p.returncode == 7
    assert p.stdout.splitlines() == ["exec", "yd-market", "/opt/ydtrader/ydtrader", "daily-report",
        "--date", "yesterday", "--catch-up-from", "2026-09-09", "--output-root", "/reports with spaces"]


def test_docker_daily_requires_explicit_container_and_start_date():
    for args in ([], ["yd-market"], ["--privileged", "2026-09-09"], ["yd-market", "yesterday"]):
        p = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True)
        assert p.returncode == 64
