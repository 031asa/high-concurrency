#!/usr/bin/env python3
"""Compile the ydcore business modules in place with Cython."""

import argparse
import shutil
from pathlib import Path

from Cython.Build import cythonize
from setuptools import Extension, setup


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_MODULES = ("launcher", "trading", "monitoring", "marketdata", "yd_redis_server", "archive_analysis")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args(argv)
    if args.clean:
        shutil.rmtree(PROJECT_ROOT / "build" / "cython-c", ignore_errors=True)
        for path in (PROJECT_ROOT / "ydcore").glob("*.so"):
            path.unlink()
    extensions = [
        Extension(
            f"ydcore.{name}",
            [str(PROJECT_ROOT / "ydcore" / f"{name}.py")],
            extra_compile_args=["-g0"],
            extra_link_args=["-Wl,--strip-all"],
        )
        for name in CORE_MODULES
    ]
    setup(
        name="ydtrader-core",
        ext_modules=cythonize(
            extensions,
            build_dir=str(PROJECT_ROOT / "build" / "cython-c"),
            compiler_directives={
                "language_level": 3,
                "binding": True,
                "linetrace": False,
                "profile": False,
                "embedsignature": False,
            },
            compile_time_env={"CYTHON_TRACE": False},
            annotate=False,
        ),
        script_args=["build_ext", "--inplace"],
        options={"build_ext": {"force": True}},
    )
    missing = [
        name
        for name in CORE_MODULES
        if not list((PROJECT_ROOT / "ydcore").glob(f"{name}.*.so"))
    ]
    if missing:
        raise SystemExit(f"missing Cython outputs: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
