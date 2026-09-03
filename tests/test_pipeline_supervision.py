"""The live runner must notice a failed publisher before the mux finishes."""
import subprocess
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "scripts/run_multi_source_aeron_mvp.sh"


def helper():
    source = RUNNER.read_text()
    return source.split("check_child_failure() {", 1)[1].split("\ncleanup() {", 1)[0]


@pytest.mark.parametrize("code", [0, 1, 7, 137])
def test_completed_child_status_is_preserved(code):
    script = "check_child_failure() {" + helper() + f"""
run_dir=/tmp
bash -c 'exit {code}' &
child=$!
# Reap deterministically; a subsequent Bash wait retains the child's status.
wait "$child" || true
check_child_failure "$child" publisher
"""
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=5)
    assert result.returncode == code
    if code:
        assert f"pipeline child failed: publisher (exit={code})" in result.stderr


def test_running_and_disabled_children_are_accepted():
    script = "check_child_failure() {" + helper() + """
run_dir=/tmp
sleep 5 &
child=$!
trap 'kill "$child" 2>/dev/null; wait "$child" 2>/dev/null' EXIT
check_child_failure "$child" publisher || exit 9
check_child_failure "" zmq-egress || exit 10
exit 0
"""
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0


def test_all_pipeline_workers_checked_while_mux_runs():
    loop = RUNNER.read_text().split('while kill -0 "$mux_pid"', 1)[1].split('wait "$mux_pid"', 1)[0]
    for process, name in [
        ("server_pid", "server"), ("publisher_pid", "publisher"),
        ("compute_pid", "compute-live"), ("audit_pid", "audit-live"),
        ("egress_pid", "zmq-egress"),
    ]:
        assert f'check_child_failure "$' + process + f'" {name}' in loop
