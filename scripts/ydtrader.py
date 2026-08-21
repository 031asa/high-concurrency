#!/usr/bin/env python3
"""Source-mode entry point; Nuitka compiles this file for deployment."""

import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")

ENTRY_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ENTRY_DIR if (ENTRY_DIR / "ydcore").is_dir() else ENTRY_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ydcore.launcher import main


if __name__ == "__main__":
    raise SystemExit(main())
