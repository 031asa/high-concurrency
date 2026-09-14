"""Install a user-level read-only probe service; requires a persistent Linux/WSL host."""
import argparse
from pathlib import Path
import subprocess
import sys

def quote(value):
    return '"' + str(value).replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    if args.interval < 30:
        parser.error("interval must be at least 30 seconds")
    root = Path(__file__).resolve().parents[1]
    config = args.config.resolve(strict=True)
    output = (args.output_root or root / "result/time-probes").resolve()
    command = [sys.executable, "-B", str(root / "main.py"), "time-probe",
               "--config", str(config), "--interval", str(args.interval),
               "--output-root", str(output)]
    unit = "[Unit]\nDescription=YDTrader read-only clock probe archive\nAfter=network-online.target\n\n"
    unit += "[Service]\nType=simple\nExecStart=" + " ".join(map(quote, command)) + "\n"
    unit += "Restart=always\nRestartSec=30\nNoNewPrivileges=true\nUMask=0027\n\n[Install]\nWantedBy=default.target\n"
    destination = Path.home() / ".config/systemd/user/ydtrader-time-probe.service"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(unit)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "ydtrader-time-probe.service"], check=True)
    subprocess.run(["systemctl", "--user", "restart", "ydtrader-time-probe.service"], check=True)
    print(destination)

if __name__ == "__main__":
    main()
