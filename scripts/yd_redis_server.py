"""Compatibility entry; all implementation lives in ydcore."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ydcore.yd_redis_server import main

if __name__ == "__main__":
    raise SystemExit(main())
