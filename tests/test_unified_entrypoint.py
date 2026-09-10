"""Local-only checks for source and extension-module dispatch; no broker login."""

import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.request import urlopen

import pytest

from ydcore import launcher


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "main.py"


@pytest.mark.parametrize("command,module_name", launcher.APPLICATION_MODULES.items())
def test_worker_dispatch_preserves_arguments_and_exit_status(monkeypatch, command, module_name):
    calls = []
    arguments = ["--file", "中文 name.json", "", "*", "--count", "2"]

    def load(name):
        calls.append(name)
        return SimpleNamespace(main=lambda argv: 17 if argv == arguments else 99)

    monkeypatch.setattr(launcher.importlib, "import_module", load)
    assert launcher.main([command, *arguments]) == 17
    assert calls == [module_name]


@pytest.mark.parametrize("command", launcher.SHELL_COMMANDS)
def test_pipeline_exec_preserves_argv_and_current_interpreter(monkeypatch, command):
    calls = []
    arguments = ["--source-config", "/tmp/两个 configs/a.json", "*", ""]
    monkeypatch.setenv("YDTRADER_PYTHON", "/incorrect/python")
    monkeypatch.setattr(launcher.os, "execve", lambda *args: calls.append(args))
    assert launcher.main([command, *arguments]) == 0
    executable, argv, environment = calls[0]
    assert executable == "/bin/bash"
    assert argv == ["bash", str(ROOT / "scripts" / launcher.SHELL_COMMANDS[command]), *arguments]
    assert environment["YDTRADER_PYTHON"] == sys.executable
    assert os.environ["YDTRADER_PYTHON"] == "/incorrect/python"


@pytest.mark.parametrize("command", launcher.SHELL_COMMANDS)
def test_pipeline_help_does_not_exec_or_import(monkeypatch, capsys, command):
    def forbidden(*args):
        pytest.fail("help tried to start a service or import a worker")

    monkeypatch.setattr(launcher.os, "execve", forbidden)
    monkeypatch.setattr(launcher.importlib, "import_module", forbidden)
    assert launcher.main([command, "--help"]) == 0
    assert f"ydtrader {command}" in capsys.readouterr().out


@pytest.mark.parametrize("module_name", launcher.APPLICATION_MODULES.values())
def test_explicit_main_arguments_ignore_process_arguments(monkeypatch, module_name):
    module = importlib.import_module(module_name)
    monkeypatch.setattr(sys, "argv", ["unrelated", "--invalid-global-argument"])
    with pytest.raises(SystemExit) as result:
        module.main(["--help"])
    assert result.value.code == 0


def test_runtime_shell_scripts_only_call_shared_python_entry():
    for name in (
        "run_aeron_mvp.sh", "run_multi_source_aeron_mvp.sh",
        "run_dashboard.sh", "run_zmq_market_smoke.sh",
    ):
        source = (ROOT / "scripts" / name).read_text()
        assert '"$PROJECT_ROOT/main.py"' in source
        for module in launcher.APPLICATION_MODULES.values():
            assert module.rsplit(".", 1)[-1] + '.py"' not in source
        subprocess.run(["bash", "-n", str(ROOT / "scripts" / name)], check=True)


def test_aeron_source_release_carries_shared_entry_and_packages():
    source = (ROOT / "aeron_mvp" / "build_release.sh").read_text()
    assert '"$PROJECT_ROOT/main.py"' in source
    assert '"$PROJECT_ROOT/ydcore/"*.py' in source
    assert '"$SCRIPT_DIR/__init__.py"' in source
    subprocess.run(["bash", "-n", str(ROOT / "aeron_mvp" / "build_release.sh")], check=True)


def _run(entry, *arguments, cwd=None):
    return subprocess.run(
        [sys.executable, str(entry), *arguments], cwd=cwd,
        capture_output=True, text=True, timeout=20,
    )


def _unused_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _stop(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)


def _check_udp_workers(entry, work):
    """Run two real synthetic workers through the mux on loopback, with bounded counts."""
    from aeron_mvp.multi_source_mux import HEADER

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as output:
        output.bind(("127.0.0.1", 0))
        output.settimeout(5)
        ports = set()
        while len(ports) < 2:
            port = _unused_port()
            if port != output.getsockname()[1]:
                ports.add(port)
        port_a, port_b = ports
        ready = work / "mux.ready"
        mux = subprocess.Popen(
            [sys.executable, str(entry), "multi-source-mux",
             "--input", f"sim-a={port_a}", "--input", f"sim-b={port_b}",
             "--output-port", str(output.getsockname()[1]),
             "--count-per-source", "3", "--source-timeout-seconds", "5",
             "--ready-file", str(ready)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and mux.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists(), "mux did not become ready"
            for port in (port_a, port_b):
                result = _run(entry, "synthetic-bridge", "--udp-port", str(port),
                              "--count", "3", "--repeat", "1", "--interval-ms", "0")
                assert result.returncode == 0, result.stderr
            packets = [output.recv(4096) for _ in range(6)]
            assert [HEADER.unpack_from(packet)[4] for packet in packets] == list(range(1, 7))
            assert {packet[-32:].split(b"\0", 1)[0] for packet in packets} == {b"sim-a", b"sim-b"}
            stdout, stderr = mux.communicate(timeout=10)
            assert mux.returncode == 0, stdout + stderr
        finally:
            _stop(mux)


def _check_dashboard(entry, work):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, str(entry), "dashboard", "--host", "127.0.0.1",
         "--port", str(port), "--result-root", str(work)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            assert process.poll() is None, process.communicate()[1]
            try:
                with urlopen(f"http://127.0.0.1:{port}/", timeout=0.5) as response:
                    assert response.status == 200
                    assert b"html" in response.read().lower()
                    return
            except OSError:
                time.sleep(0.05)
        pytest.fail("dashboard did not become ready")
    finally:
        _stop(process)


def test_source_entry_runs_local_workers_and_dashboard(tmp_path):
    _check_udp_workers(ENTRY, tmp_path)
    _check_dashboard(ENTRY, tmp_path)


@pytest.mark.parametrize("command", launcher.SHELL_COMMANDS)
def test_real_pipeline_argument_errors_keep_shell_exit_status(command):
    result = _run(ENTRY, command, "--invalid-option")
    assert result.returncode == 64
    assert "unknown option" in result.stderr


def test_ctp_cli_passes_parsed_arguments_to_lazy_native_runner(monkeypatch):
    from aeron_mvp import ctp_cli

    calls = []
    monkeypatch.setitem(sys.modules, "aeron_mvp.ctp_bridge", SimpleNamespace(
        run=lambda args: calls.append(args) or 9,
    ))
    assert ctp_cli.main(["--instruments", "IF2609, IC2609", "--flow-path", "/tmp/flow path"]) == 9
    assert calls[0].instruments == ["IF2609", "IC2609"]
    assert calls[0].flow_path == "/tmp/flow path"


@pytest.mark.skipif(
    os.environ.get("RUN_COMPILED_ENTRYPOINT_TEST") != "1",
    reason="opt-in Cython toolchain test: RUN_COMPILED_ENTRYPOINT_TEST=1",
)
def test_compiled_modules_work_without_their_python_sources(tmp_path):
    """Build a disposable extension-only application, NOT a complete release image."""
    app = tmp_path / "app"
    for directory in ("ydcore", "aeron_mvp", "dashboard"):
        target = app / directory
        target.mkdir(parents=True)
        for source in (ROOT / directory).iterdir():
            if source.suffix in {".py", ".html"}:
                shutil.copy2(source, target / source.name)
    # Redis worker imports the shared trading core and its error catalogue.
    (app / "data").mkdir()
    shutil.copy2(ROOT / "data" / "error_code.csv", app / "data" / "error_code.csv")
    # Preserve local Python support modules used by the current dashboard.
    (app / "scripts").mkdir()
    for support in (ROOT / "scripts").glob("*.py"):
        if support.name in {"analyze_archive.py", "yd_redis_server.py"}:
            continue  # These source-only wrappers are excluded from releases.
        shutil.copy2(support, app / "scripts" / support.name)
    shutil.copy2(ENTRY, app / ENTRY.name)
    entry = app / ENTRY.name
    config = tmp_path / "source config.json"
    config.write_text(json.dumps({"schema_version": 1, "name": "sim-a", "kind": "synthetic", "instrument": "IC2609"}))
    commands = [["--help"], ["aeron", "--help"], ["multi-source", "--help"]]
    commands += [[name, "--help"] for name in launcher.APPLICATION_MODULES]
    commands += [["source-config", "--project-root", str(app), "--config", str(config)]]
    before = [_run(entry, *arguments, cwd=tmp_path) for arguments in commands]
    for arguments, result in zip(commands, before):
        assert result.returncode == 0, (arguments, result.returncode, result.stderr)
    names = ["ydcore.launcher", "ydcore.trading", "aeron_mvp.market_wire", "aeron_mvp.ctp_bridge", *launcher.APPLICATION_MODULES.values()]
    names = list(dict.fromkeys(names))
    sources = [name.replace(".", "/") + ".py" for name in names]
    setup = tmp_path / "setup_extensions.py"
    setup.write_text(
        "from setuptools import Extension, setup\nfrom Cython.Build import cythonize\n"
        f"setup(ext_modules=cythonize([Extension(n, [p]) for n,p in {list(zip(names, sources))!r}], "
        "compiler_directives={'language_level': 3}))\n"
    )
    built = subprocess.run(
        [sys.executable, str(setup), "build_ext", "--inplace"], cwd=app,
        capture_output=True, text=True, timeout=240,
    )
    assert built.returncode == 0, built.stdout[-4000:] + built.stderr[-4000:]
    for source in sources:
        path = app / source
        assert list(path.parent.glob(path.stem + ".*.so")), source
        path.unlink()
    after = [_run(entry, *arguments, cwd=tmp_path) for arguments in commands]
    assert [(r.returncode, r.stdout, r.stderr) for r in after] == [
        (r.returncode, r.stdout, r.stderr) for r in before
    ]
    _check_udp_workers(entry, tmp_path)
    _check_dashboard(entry, tmp_path)
