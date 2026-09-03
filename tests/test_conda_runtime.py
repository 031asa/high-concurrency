import shlex
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "utils" / "conda_runtime.sh"


def run_project_check(*, system: str, wsl: bool, resolved_path: str):
    script = f"""
source {shlex.quote(str(RUNTIME))}
uname() {{ printf '%s\\n' {shlex.quote(system)}; }}
grep() {{ return {0 if wsl else 1}; }}
readlink() {{ printf '%s\\n' {shlex.quote(resolved_path)}; }}
ydtrader_require_linux_project /project
"""
    return subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )


def test_native_linux_project_is_accepted():
    process = run_project_check(
        system="Linux",
        wsl=False,
        resolved_path="/srv/high-concurrency",
    )

    assert process.returncode == 0, process.stderr


def test_native_linux_mount_is_not_mistaken_for_windows_storage():
    process = run_project_check(
        system="Linux",
        wsl=False,
        resolved_path="/mnt/market-data/high-concurrency",
    )

    assert process.returncode == 0, process.stderr


def test_wsl_project_on_windows_mount_is_rejected():
    process = run_project_check(
        system="Linux",
        wsl=True,
        resolved_path="/mnt/c/high-concurrency",
    )

    assert process.returncode == 2
    assert "repository must be in the WSL Linux filesystem" in process.stderr


def test_non_linux_host_is_rejected():
    process = run_project_check(
        system="Darwin",
        wsl=False,
        resolved_path="/project",
    )

    assert process.returncode == 2
    assert "Linux is required" in process.stderr
