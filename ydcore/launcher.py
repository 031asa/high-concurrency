"""Single command dispatcher for all YDTrader functions."""

from __future__ import annotations

import importlib
import sys


BUSINESS_MODULES = {
    "order": "ydcore.trading",
    "monitor": "ydcore.monitoring",
    "marketdata": "ydcore.marketdata",
}

USAGE = """usage: ydtrader <command> [options]

commands:
  order [original options]     trading, control, query, and cancel functions
  monitor [original options]   independent account monitor
  marketdata [original options] subscribe and compare market timestamps

Commands start directly. YDApi account credentials are still read from the
local account configuration because they are required to connect to the broker.
"""


def _dispatch(command, argv):
    module = importlib.import_module(BUSINESS_MODULES[command])
    return int(module.run(argv))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(USAGE)
        return 0
    command, command_argv = argv[0], argv[1:]
    if command not in BUSINESS_MODULES:
        print(f"未知命令: {command}\n\n{USAGE}", file=sys.stderr)
        return 2
    try:
        return _dispatch(command, command_argv)
    except KeyboardInterrupt:
        print("操作已取消", file=sys.stderr)
        return 130
