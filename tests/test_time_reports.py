import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def report(platform, offset=5.0):
    return {
        "schema": 1,
        "platform": platform,
        "hostname": platform,
        "generated_at_utc": "2026-08-21T06:00:00.000Z",
        "authority": {
            "name": "Cloudflare Time Services",
            "url": "https://www.cloudflare.com/time/",
            "ntp_servers": ["time.cloudflare.com"],
            "environment": "test",
        },
        "authority_minus_local_ms": offset,
        "max_abs_sample_ms": abs(offset),
        "max_offset_ms": 50,
        "max_cross_difference_ms": 50,
        "pass": True,
        "failure": "",
    }


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell 5.1 integration test")
@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("pass", 0),
        ("authority", 2),
        ("website", 2),
        ("stale", 2),
        ("endpoint", 2),
        ("cross", 2),
        ("ntp", 2),
    ],
)
def test_compare_time_report_samples(tmp_path, mutation, expected):
    windows = report("windows", 5.0)
    linux = report("linux", 7.0)
    if mutation == "authority":
        linux["authority"]["name"] = "different"
    elif mutation == "website":
        linux["authority"]["url"] = "https://example.invalid/"
    elif mutation == "stale":
        linux["generated_at_utc"] = "2026-08-21T06:02:00.000Z"
    elif mutation == "endpoint":
        linux["pass"] = False
        linux["failure"] = "no valid NTP sample"
    elif mutation == "cross":
        linux["authority_minus_local_ms"] = 100.0
    elif mutation == "ntp":
        linux["authority"]["ntp_servers"] = ["wrong.example"]
    windows_path = tmp_path / "windows.json"
    linux_path = tmp_path / "linux.json"
    windows_path.write_text(json.dumps(windows), encoding="utf-8")
    linux_path.write_text(json.dumps(linux), encoding="utf-8")
    process = subprocess.run(
        [
            shutil.which("powershell.exe"),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "compare_time_reports.ps1"),
            "-WindowsReport",
            str(windows_path),
            "-LinuxReport",
            str(linux_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert process.returncode == expected, process.stdout + process.stderr


def test_production_template_has_no_public_default():
    template = (ROOT / "config" / "time_authority.cffex.example.conf").read_text(encoding="utf-8")
    assert "environment=production" in template
    assert "REPLACE_WITH_CFFEX_NTP_HOST_OR_IP" in template
    assert "cloudflare" not in template.lower()


def test_south_china_fallback_is_not_labeled_as_production_or_cffex():
    fallback = (ROOT / "config" / "time_authority.tencent-south-china-fallback.conf").read_text(encoding="utf-8")
    assert "environment=test" in fallback
    assert "ntp4.tencent.com ntp5.tencent.com ntp2.tencent.com" in fallback
    assert "authority_name=Tencent Cloud Public NTP" in fallback
    assert "environment=production" not in fallback


def test_old_phc_setup_script_is_removed():
    assert not (ROOT / "scripts" / "setup_wsl_chrony_windows_sync.sh").exists()
