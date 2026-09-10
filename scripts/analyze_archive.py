"""Source-checkout compatibility entry; packaged users invoke main.py analyze-archive."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ydcore.archive_analysis import main

if __name__ == "__main__":
    raise SystemExit(main())
