"""Single authorized command dispatcher for all YDTrader functions."""

from __future__ import annotations

import getpass
import importlib
import sys
from pathlib import Path

from . import licensing


BUSINESS_MODULES = {
    "order": "ydcore.trading",
    "monitor": "ydcore.monitoring",
    "marketdata": "ydcore.marketdata",
}

USAGE = """usage: ydtrader <command> [options]

commands:
  machine-code                 print this Linux machine request code
  activate --license FILE      activate once as root and start the 24-hour timer
  order [original options]     trading, control, query, and cancel functions
  monitor [original options]   independent account monitor
  marketdata [original options] subscribe and compare market timestamps

Business commands prompt for the license password on every start.
"""


def _license_error(exc):
    print(f"授权失败: {exc}", file=sys.stderr)
    return exc.exit_code


def _password_attempts(action):
    last_error = None
    for attempt in range(1, 4):
        password = getpass.getpass("运行密码: ")
        try:
            return action(password)
        except licensing.LicenseInvalidError as exc:
            last_error = exc
            if attempt < 3:
                print(f"密码或许可证无效，剩余尝试次数: {3 - attempt}", file=sys.stderr)
    raise last_error


def _dispatch(command, argv):
    module = importlib.import_module(BUSINESS_MODULES[command])
    return int(module.run(argv))


def _activate(argv):
    if len(argv) != 2 or argv[0] != "--license":
        print("usage: ydtrader activate --license FILE", file=sys.stderr)
        return 2
    source = Path(argv[1]).expanduser().resolve()
    grant = _password_attempts(lambda password: licensing.activate(source, password))
    print(
        "激活成功: "
        f"license_id={grant.license_id} "
        f"expires_at={grant.expires_at.isoformat()}"
    )
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(USAGE)
        return 0
    command, command_argv = argv[0], argv[1:]
    try:
        if command == "machine-code":
            if command_argv:
                print("usage: ydtrader machine-code", file=sys.stderr)
                return 2
            print(licensing.machine_code())
            return 0
        if command == "activate":
            return _activate(command_argv)
        if command not in BUSINESS_MODULES:
            print(f"未知命令: {command}\n\n{USAGE}", file=sys.stderr)
            return 2
        if any(value in {"-h", "--help"} for value in command_argv):
            return _dispatch(command, command_argv)
        _password_attempts(
            lambda password: licensing.verify_business_access(command, password)
        )
        return _dispatch(command, command_argv)
    except licensing.LicenseError as exc:
        return _license_error(exc)
    except KeyboardInterrupt:
        print("操作已取消", file=sys.stderr)
        return 130
