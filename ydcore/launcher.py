"""Single command dispatcher for all YDTrader functions."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


BUSINESS_MODULES = {
    "order": "ydcore.trading",
    "monitor": "ydcore.monitoring",
    "marketdata": "ydcore.marketdata",
}

# Import on demand: top-level help must not load native broker APIs or start services.
APPLICATION_MODULES = {
    "daily-report": "timer_pdf.code.main",
    "version-info": "ydcore.version_info",
    "time-probe": "scripts.time_probe",
    "analyze-archive": "ydcore.archive_analysis",
    "yd-redis-server": "ydcore.yd_redis_server",
    "redis-trader": "ydcore.yd_redis_server",
    "dashboard": "dashboard.server",
    "source-config": "aeron_mvp.source_config",
    "multi-source-mux": "aeron_mvp.multi_source_mux",
    "synthetic-bridge": "aeron_mvp.synthetic_bridge",
    "ctp-bridge": "aeron_mvp.ctp_cli",
    "ydapi-bridge": "aeron_mvp.ydapi_bridge",
    "zmq-probe": "aeron_mvp.zmq_market_probe",
}
SHELL_COMMANDS = {
    "aeron": "run_aeron_mvp.sh",
    "multi-source": "run_multi_source_aeron_mvp.sh",
}
SHELL_HELP = {
    "aeron": "usage: ydtrader aeron [--source synthetic|ctp|ydapi] [--count N] [original options]",
    "multi-source": (
        "usage: ydtrader multi-source [--source-config FILE [FILE ...]] "
        "[--sources synthetic:sim-a,synthetic:sim-b] [--count N] "
        "[--zmq-endpoint tcp://HOST:PORT] [original options]"
    ),
}

USAGE = """usage: ydtrader <command> [options]

commands:
  daily-report [options]      independent PDF and pending CSV report
  version-info [options]      read-only deployed version identity
  time-probe [options]        read-only clock probe evidence
  analyze-archive [options]    read-only analysis of recorded market data
  yd-redis-server [options]    alias for redis-trader
  redis-trader [options]      independent Redis trading service
  order [original options]     trading, control, query, and cancel functions
  monitor [original options]   independent account monitor
  marketdata [original options] subscribe and compare market timestamps
  aeron [original options]     single-source Aeron pipeline
  multi-source [options]       multi-source bridges, Aeron, archive and optional ZMQ
  dashboard [options]          HTTP dashboard (run separately from the pipeline)

Pipeline worker commands (also support --help):
  source-config, multi-source-mux, synthetic-bridge, ctp-bridge,
  ydapi-bridge, zmq-probe

Commands start directly. YDApi account credentials are still read from the
local account configuration because they are required to connect to the broker.
"""


def _dispatch(command, argv):
    if command in APPLICATION_MODULES:
        module = importlib.import_module(APPLICATION_MODULES[command])
        return int(module.main(argv))
    if command in SHELL_COMMANDS:
        if argv == ["--help"] or argv == ["-h"]:
            print(SHELL_HELP[command])
            return 0
        script = Path(__file__).resolve().parents[1] / "scripts" / SHELL_COMMANDS[command]
        if not script.is_file():
            print(f"启动脚本不存在: {script}", file=sys.stderr)
            return 2
        environment = os.environ.copy()
        environment["YDTRADER_PYTHON"] = sys.executable
        # Replace the dispatcher so existing Shell traps receive container signals.
        os.execve("/bin/bash", ["bash", str(script), *argv], environment)
        return 0  # only reachable when execve is replaced by a test double
    module = importlib.import_module(BUSINESS_MODULES[command])
    return int(module.run(argv))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(USAGE)
        return 0
    command, command_argv = argv[0], argv[1:]
    if (
        command not in BUSINESS_MODULES
        and command not in APPLICATION_MODULES
        and command not in SHELL_COMMANDS
    ):
        print(f"未知命令: {command}\n\n{USAGE}", file=sys.stderr)
        return 2
    try:
        return _dispatch(command, command_argv)
    except KeyboardInterrupt:
        print("操作已取消", file=sys.stderr)
        return 130
