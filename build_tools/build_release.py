#!/usr/bin/env python3
"""Build and sign the CPython 3.9 Linux standalone release."""

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = PROJECT_ROOT / "build"
RESULT_ROOT = PROJECT_ROOT / "result"
RELEASE_NAME = "ydtrader-linux-x86_64"
TIME_MANUAL = "README_授时与行情延迟操作手册.md"


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=PROJECT_ROOT, check=True)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_private_key(path):
    from getpass import getpass
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    raw = path.read_bytes()
    try:
        return load_pem_private_key(raw, password=None)
    except TypeError:
        password = getpass("发证私钥密码: ").encode("utf-8")
        return load_pem_private_key(raw, password=password)


def copy_release_support_files(release):
    docs = release / "docs"
    tools = release / "tools"
    config = release / "config"
    docs.mkdir()
    tools.mkdir()
    config.mkdir()
    shutil.copy2(PROJECT_ROOT / TIME_MANUAL, docs / TIME_MANUAL)
    for name in ("setup_linux_time_sync.sh", "linux_time_report.sh"):
        target = tools / name
        shutil.copy2(PROJECT_ROOT / "scripts" / name, target)
        os.chmod(target, 0o755)
    shutil.copy2(
        PROJECT_ROOT / "config" / "time_authority.cffex.example.conf",
        config / "time_authority.cffex.example.conf",
    )
    shutil.copy2(
        PROJECT_ROOT / "config" / "time_authority.tencent-south-china-fallback.conf",
        config / "time_authority.tencent-south-china-fallback.conf",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-key", required=True, type=Path)
    parser.add_argument("--private-key", required=True, type=Path)
    args = parser.parse_args(argv)

    if sys.version_info[:2] != (3, 9):
        raise SystemExit("release must be built with CPython 3.9")
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise SystemExit("release target must be Linux x86_64")
    if not args.public_key.is_file() or not args.private_key.is_file():
        raise SystemExit("key file not found")

    shutil.rmtree(BUILD_ROOT / "nuitka", ignore_errors=True)
    shutil.rmtree(RESULT_ROOT / RELEASE_NAME, ignore_errors=True)
    run(sys.executable, "build_tools/build_cython.py", "--clean", "--public-key", args.public_key)
    run(
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--remove-output",
        "--output-dir=build/nuitka",
        "--output-filename=ydtrader",
        "--include-package=argon2",
        "--include-package=cryptography",
        "--include-module=pyyd",
        "--include-module=ydcore.trading",
        "--include-module=ydcore.monitoring",
        "--include-module=ydcore.marketdata",
        "--report=build/nuitka/build-report.xml",
        "scripts/ydtrader.py",
    )

    dist = BUILD_ROOT / "nuitka" / "ydtrader.dist"
    binary = dist / "ydtrader"
    if not binary.is_file():
        raise SystemExit(f"Nuitka output missing: {binary}")
    release = RESULT_ROOT / RELEASE_NAME
    app = release / "app"
    shutil.copytree(dist, app)
    (app / "config").mkdir()
    shutil.copy2(PROJECT_ROOT / "config" / "account.example.json", app / "config")
    shutil.copy2(PROJECT_ROOT / "config" / "monitor.json", app / "config")
    shutil.copy2(PROJECT_ROOT / "error_code.csv", app / "error_code.csv")
    shutil.copytree(PROJECT_ROOT / "install", release / "install")
    shutil.copy2(args.public_key, release / "install" / "ydtrader-public.pem")
    copy_release_support_files(release)
    for required in (app / "pyyd.so", app / "yd.so"):
        if not required.is_file():
            raise SystemExit(f"vendor runtime missing from standalone output: {required}")

    forbidden = {".py", ".pyc", ".c", ".pdb"}
    leaked = [path for path in app.rglob("*") if path.is_file() and path.suffix.lower() in forbidden]
    if leaked:
        raise SystemExit("forbidden source/debug artifacts: " + ", ".join(map(str, leaked)))
    lines = []
    for path in sorted(path for path in app.rglob("*") if path.is_file()):
        relative = path.relative_to(app).as_posix()
        if relative.startswith(("config/", "logs/")):
            continue
        lines.append(f"{sha256(path)}  {relative}\n")
    manifest = release / "install_manifest.sha256"
    manifest.write_text("".join(lines), encoding="ascii")
    private_key = load_private_key(args.private_key)
    (release / "install_manifest.sig").write_bytes(private_key.sign(manifest.read_bytes()))
    os.chmod(release / "install" / "ydtrader-destroy", 0o755)
    os.chmod(release / "install" / "install_linux.sh", 0o755)

    archive = RESULT_ROOT / f"{RELEASE_NAME}.tar.gz"
    archive.unlink(missing_ok=True)
    with tarfile.open(archive, "w:gz") as output:
        output.add(release, arcname=RELEASE_NAME)
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
