"""Plaintext entrypoint: only forwards command-line arguments."""

import sys

from ydcore.launcher import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
