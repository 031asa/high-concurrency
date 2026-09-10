"""Read-only analysis of this project's unfragmented Aeron/SBE v3 recordings.

No broker connection, Java process, or third-party Python package is required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import mmap
import sqlite3
import struct
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CHINA = timezone(timedelta(hours=8))
NS = 1_000_000_000


def day_of(ns):
    if ns is None:
        return None
    try:
        return datetime.fromtimestamp(ns / NS, CHINA).date().isoformat() if ns > 0 else None
    except (ValueError, OverflowError, OSError):
        return None


def iso(ns):
    return datetime.fromtimestamp(ns / NS, CHINA).isoformat() if day_of(ns) else None


def quotes(path, *, snapshot_size=None, include_digest=False):
    """Frame lengths exclude alignment padding. Zero length marks unused tail.

    Only this project's complete, unfragmented schema 701/template 1/v2-v3 is
    supported. Unsupported or truncated frames fail loudly instead of returning
    misleading statistics. Analyse stopped runs; never truncate an open archive.
    Replay may bound its read to snapshot_size and request per-frame digests;
    the default analysis output remains unchanged.
    """
    size = path.stat().st_size if snapshot_size is None else snapshot_size
    if not size:
        return
    with path.open("rb") as stream, mmap.mmap(stream.fileno(), size, access=mmap.ACCESS_READ) as data:
        offset = 0
        while offset + 32 <= len(data):
            length, version, flags, kind = struct.unpack_from("<iBBH", data, offset)
            if length == 0:
                break
            if length < 32 or offset + length > len(data) or version != 0:
                raise ValueError(f"{path}:{offset}: invalid/incomplete Aeron frame")
            if kind == 0:  # Aeron term padding
                offset += (length + 31) & ~31
                continue
            if kind != 1 or flags != 192 or length < 40:
                raise ValueError(f"{path}:{offset}: unsupported or fragmented frame")
            block, template, schema, sbe_version = struct.unpack_from("<HHHH", data, offset + 32)
            if (schema, template, sbe_version, block) not in ((701, 1, 3, 324), (701, 1, 2, 194)):
                raise ValueError(f"{path}:{offset}: unsupported SBE {schema}/{template}/{sbe_version}/{block}")
            base, end = offset + 40, offset + length
            cursor = base + block
            if cursor > end:
                raise ValueError(f"{path}:{offset}: truncated SBE block")
            fields = []
            for _ in range(8 if sbe_version == 3 else 4):
                if cursor + 2 > end:
                    raise ValueError(f"{path}:{offset}: truncated string length")
                size = struct.unpack_from("<H", data, cursor)[0]
                cursor += 2
                if size > 1024 or cursor + size > end:
                    raise ValueError(f"{path}:{offset}: invalid string length")
                fields.append(data[cursor:cursor + size].decode("utf-8"))
                cursor += size
            if include_digest and cursor != end:
                raise ValueError(f"{path}:{offset}: unexpected trailing SBE bytes")
            sequence, market, received, price = struct.unpack_from("<QQQd", data, base)
            digest = {"digest": hashlib.sha256(data[offset + 32:end]).hexdigest()} if include_digest else {}
            yield dict(session=fields[0], sequence=sequence, market=market, received=received,
                       price=price if math.isfinite(price) else None,
                       valid=data[base + 64] == 1, contract=fields[1],
                       source=fields[7] if sbe_version == 3 else "", market_raw=fields[3], frame_offset=offset, **digest)
            offset += (length + 31) & ~31
        if include_digest and offset < len(data) and len(data) - offset < 32 and any(data[offset:]):
            raise ValueError(f"{path}:{offset}: incomplete tail header")


def stats(db, where, args):
    n, mean, maximum = db.execute(
        f"SELECT count(*),avg(latency),max(latency) FROM ticks WHERE {where} AND latency IS NOT NULL",
        args).fetchone()
    result = dict(count=n, mean_ms=mean, std_ms=None, max_ms=maximum)
    for pct in (50, 90, 95, 99):
        result[f"p{pct}_ms"] = None
    if n:
        variance = db.execute(
            f"SELECT avg((latency-?)*(latency-?)) FROM ticks WHERE {where} AND latency IS NOT NULL",
            [mean, mean, *args]).fetchone()[0]
        result["std_ms"] = math.sqrt(max(0, variance))
        for pct in (50, 90, 95, 99):
            rank = max(0, math.ceil(n * pct / 100) - 1)
            result[f"p{pct}_ms"] = db.execute(
                f"SELECT latency FROM ticks WHERE {where} AND latency IS NOT NULL ORDER BY latency LIMIT 1 OFFSET ?",
                [*args, rank]).fetchone()[0]
    return result


def analyse(run_dir, selected_date=None):
    run_dir = Path(run_dir).resolve()
    meta_file = run_dir / "run.meta"
    if not meta_file.is_file():
        raise ValueError("run-dir must contain run.meta and archive/")
    meta = dict(line.split("=", 1) for line in meta_file.read_text().splitlines() if "=" in line)
    paths = list((run_dir / "archive").glob("*.rec"))
    paths.sort(key=lambda p: tuple(int(x) for x in p.stem.split("-")))
    if not paths:
        raise ValueError(f"No .rec files in {run_dir / 'archive'}")
    report = dict(run_id=meta.get("run_id", run_dir.name), run_dir=str(run_dir),
                  date=selected_date, timezone="Asia/Shanghai",
                  generated_at=datetime.now(CHINA).isoformat(),
                  run_status=meta.get("status", "UNKNOWN"), raw_records=0, duplicates=0,
                  sequence_gap_events=0, missing_sequence_positions=0, non_increasing_events=0,
                  files=[str(p) for p in paths], sources={},
                  notes=[
                      "只分析本批次归档；归档中未出现的上游行情无法据此计算丢包率。",
                      "间隔按同来源本地回调接收时间计算；超过60秒不直接等于断线，需人工对照休市与日志。",
                      "当日延迟仅包含事件自然日与接收自然日一致的有效实盘记录；不使用TradingDay。",
                      "延迟为两个时间戳的绝对差，包含时钟误差，不是纯网络延迟；不设大延迟阈值。",
                      "分位数为原始样本精确nearest-rank，标准差为总体标准差；非HdrHistogram近似。",
                      "累计延迟为本批次该实盘来源所有时间有效样本，含跨日快照；与Dashboard首笔过滤口径可能不同。",
                      "按session+sequence去重，不把不同序号的相同价格行情当重复。",
                      "仅支持本项目从段首开始的未分片SBE v2/v3归档；零帧视为未使用尾部，建议分析停止的批次。",
                  ])
    # Disk-backed scratch storage keeps raw quote count out of Python RAM.
    with tempfile.TemporaryDirectory(prefix="aeron-analysis-") as scratch:
        with sqlite3.connect(str(Path(scratch) / "ticks.sqlite")) as db:
            db.execute("CREATE TABLE ticks(session TEXT, seq TEXT, source TEXT, contract TEXT, received INTEGER, day TEXT, category TEXT, latency REAL, price REAL, market_raw TEXT, PRIMARY KEY(session,seq))")
            last = {}
            modes = {}
            for path in paths:
                recording = path.stem.split("-")[0]
                for q in quotes(path):
                    report["raw_records"] += 1
                    key = (recording, q["session"])
                    previous = last.get(key)
                    if previous is not None:
                        if q["sequence"] > previous + 1:
                            report["sequence_gap_events"] += 1
                            report["missing_sequence_positions"] += q["sequence"] - previous - 1
                        elif q["sequence"] <= previous:
                            report["non_increasing_events"] += 1
                    last[key] = q["sequence"]
                    source = q["source"] or meta.get("source", "unknown")
                    mode = meta.get(f"source_latency_mode.{source}", meta.get("latency_mode", "unknown"))
                    modes[source] = mode
                    receive_day, event_day = day_of(q["received"]), day_of(q["market"])
                    category, latency = "valid", None
                    if not receive_day or not event_day or not q["valid"]:
                        category = "invalid_time"
                    elif event_day < receive_day:
                        category = "old_snapshot"
                    elif event_day > receive_day:
                        category = "future_date"
                    if mode == "live" and receive_day and event_day and q["valid"]:
                        latency = abs(q["received"] - q["market"]) / 1_000_000
                    changes = db.total_changes
                    db.execute("INSERT OR IGNORE INTO ticks VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (q["session"], str(q["sequence"]), source, q["contract"],
                                q["received"], receive_day, category, latency, q["price"], q["market_raw"]))
                    if db.total_changes == changes:
                        report["duplicates"] += 1
            db.commit()
            db.execute("CREATE INDEX source_time ON ticks(source,received)")
            configured = meta.get("sources", meta.get("source", "")).split(",")
            names = list(dict.fromkeys([n for n in configured if n and n != "multi"] + list(modes)))
            for source in names:
                where, args = "source=?", [source]
                if selected_date:
                    where += " AND day=?"
                    args.append(selected_date)
                count, first, final = db.execute(
                    f"SELECT count(*),min(received),max(received) FROM ticks WHERE {where}", args).fetchone()
                mode = modes.get(source, meta.get(f"source_latency_mode.{source}", meta.get("latency_mode", "unknown")))
                view = dict(mode=mode, archived_records=count, first_received=iso(first), last_received=iso(final),
                            same_day_latency=stats(db, where + " AND category='valid'", args),
                            session_total_latency=stats(db, "source=?", [source]),
                            categories=dict(db.execute(f"SELECT category,count(*) FROM ticks WHERE {where} GROUP BY category", args)),
                            contracts=dict(db.execute(f"SELECT contract,count(*) FROM ticks WHERE {where} GROUP BY contract", args)),
                            gaps_over_60s=[], gaps_over_60s_count=0)
                previous = None
                for (received,) in db.execute(f"SELECT received FROM ticks WHERE {where} AND received>0 ORDER BY received", args):
                    if previous is not None and received - previous > 60 * NS:
                        view["gaps_over_60s_count"] += 1
                        if len(view["gaps_over_60s"]) < 1000:
                            view["gaps_over_60s"].append(dict(start=iso(previous), end=iso(received), seconds=(received-previous)/NS))
                    previous = received
                latest = db.execute(f"SELECT contract,price,market_raw FROM ticks WHERE {where} ORDER BY received DESC LIMIT 1", args).fetchone()
                view["latest_quote"] = dict(zip(("contract", "last_price", "market_time"), latest)) if latest else None
                report["sources"][source] = view
            report["invalid_receive_time_records_all_dates"] = db.execute("SELECT count(*) FROM ticks WHERE day IS NULL").fetchone()[0]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--date", type=date.fromisoformat, help="北京时间接收自然日 YYYY-MM-DD；省略则分析整批次")
    parser.add_argument("--output", type=Path, help="JSON文件；省略则输出到终端")
    args = parser.parse_args(argv)
    try:
        if args.output and (args.output.resolve() == args.run_dir.resolve() or args.run_dir.resolve() in args.output.resolve().parents):
            raise ValueError("output must be outside run-dir to protect original data")
        report = analyse(args.run_dir, args.date.isoformat() if args.date else None)
        text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
            print(f"报告已输出：{args.output.resolve()}")
        else:
            print(text)
        return 0
    except (ValueError, OSError, sqlite3.Error, struct.error) as exc:
        parser.exit(1, f"分析失败：{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
