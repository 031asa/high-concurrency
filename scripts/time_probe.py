"""Read-only chronyd -Q sampling with durable, per-probe JSON evidence."""
import argparse
import getpass
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import tempfile
from datetime import datetime, timezone, timedelta
import uuid

CHINA = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]


def probe(server, limit, timeout=20):
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", server):
        raise ValueError("Invalid NTP server")
    started = datetime.now(timezone.utc)
    command = ["chronyd", "-Q", "-U", "-u", getpass.getuser(), "-t", str(timeout),
               "server " + server + " iburst", "cmdport 0"]
    begin = time.monotonic()
    offset = None
    code = None
    failure = ""
    try:
        with tempfile.TemporaryDirectory(prefix="ydtrader-probe-") as temporary:
            command.append("pidfile " + temporary + "/chronyd.pid")
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 5,
                                    env={**os.environ, "LC_ALL": "C"})
        code = result.returncode
        raw = result.stdout + result.stderr
        match = re.search(r"System clock wrong by ([-+0-9.]+) seconds", raw)
        if code == 0 and match:
            offset = float(match.group(1)) * 1000
            if not math.isfinite(offset):
                offset = None
        if offset is None:
            failure = "No valid NTP offset; see raw_output"
    except (OSError, subprocess.TimeoutExpired) as error:
        raw = str(error)
        failure = type(error).__name__
    return {"schema": 1, "probe_id": uuid.uuid4().hex, "started_at_utc": started.isoformat(),
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "date_beijing": started.astimezone(CHINA).date().isoformat(),
            "hostname": socket.gethostname(), "platform": "linux",
            "server": server, "method": "chronyd -Q", "adjusts_clock": False,
            "offset_definition": "authority_minus_local_ms", "offset_ms": offset,
            "max_offset_ms": limit, "duration_ms": (time.monotonic() - begin) * 1000,
            "rtt_ms": None, "uncertainty_ms": None,
            "status": "ERROR" if offset is None else ("PASS" if abs(offset) <= limit else "EXCEEDED"),
            "failure": failure, "returncode": code, "command": command, "raw_output": raw}


def save(root, record):
    directory = root / record["date_beijing"]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (record["probe_id"] + ".json")
    temporary = target.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, target)
    finally:
        temporary.unlink()
    descriptor = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return target


def history(root, date=None):
    date = date or datetime.now(CHINA).date().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("Use YYYY-MM-DD")
    datetime.strptime(date, "%Y-%m-%d")
    rows = []
    unreadable = 0
    for path in (root / date).glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("schema") != 1 or record.get("date_beijing") != date:
                raise ValueError("Invalid evidence schema/date")
            for key in ("started_at_utc", "hostname", "server", "status"):
                if not isinstance(record.get(key), str):
                    raise ValueError("Missing evidence field")
            rows.append(record)
        except (OSError, ValueError, AttributeError):
            unreadable += 1
    rows.sort(key=lambda r: r["started_at_utc"])
    groups = {}
    for row in rows:
        key = (row["hostname"], row["server"])
        group = groups.setdefault(key, {"hostname": key[0], "server": key[1], "count": 0,
                                        "failed": 0, "exceeded": 0, "values": []})
        group["count"] += 1
        value = row.get("offset_ms")
        if isinstance(value, (int, float)) and math.isfinite(value) and row["status"] in ("PASS", "EXCEEDED"):
            group["values"].append(value)
            group["exceeded"] += row["status"] == "EXCEEDED"
        else:
            group["failed"] += 1
    for group in groups.values():
        values = group.pop("values")
        group.update(valid=len(values), mean_ms=sum(values) / len(values) if values else None,
                     max_abs_ms=max(map(abs, values)) if values else None)
    return {"date": date, "root": str(root), "rows": rows, "groups": list(groups.values()),
            "unreadable_files": unreadable, "latest": rows[-1] if rows else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=ROOT / "result/time-probes")
    parser.add_argument("--interval", type=int, default=0, help="Seconds; 0 = one round")
    args = parser.parse_args(argv)
    if args.interval < 0:
        parser.error("interval must be non-negative")
    while True:
        config = dict(line.split("=", 1) for line in args.config.read_text().splitlines()
                      if "=" in line and not line.lstrip().startswith("#"))
        servers = config["ntp_servers"].split()
        limit = float(config["max_offset_ms"])
        if not servers or not math.isfinite(limit) or limit < 0:
            raise ValueError("Invalid servers/threshold")
        for server in servers:
            record = probe(server, limit)
            record["authority_name"] = config.get("authority_name", "")
            record["authority_environment"] = config.get("environment", "")
            print(str(save(args.output_root, record)), record["status"], flush=True)
        if not args.interval:
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    main()
