"""Read-only, disk-backed historical quote browsing. Never starts a pipeline."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import struct
import threading
from contextlib import nullcontext
from datetime import date, datetime, timezone
from pathlib import Path

from aeron_mvp.market_wire import decode_market_quote
from ydcore.archive_analysis import CHINA, day_of, quotes

PARSER_VERSION = 4


def receive_iso(ns):
    return datetime.fromtimestamp(ns // 1_000_000_000, CHINA).strftime("%Y-%m-%dT%H:%M:%S") + f".{ns % 1_000_000_000:09d}+08:00"


class ReplayStore:
    def __init__(self, root, cache):
        self.root, self.cache = Path(root).resolve(), Path(cache).resolve()
        if self.cache == self.root or self.root in self.cache.parents:
            raise ValueError("回放缓存不能放进原始归档目录")
        self.lock = threading.Lock()
        self.state = {"status": "idle"}
        self.path = self.cache / "index.sqlite"

    def refresh(self):
        with self.lock:
            if self.state["status"] == "loading":
                return
            self.state = {"status": "loading"}
            threading.Thread(target=self._build, daemon=True).start()

    def _build(self):
        temporary = None
        try:
            if not self.root.is_dir():
                raise ValueError("原始归档根目录不存在或不可读")
            self.cache.mkdir(parents=True, exist_ok=True)
            files, warnings = [], []
            for run in sorted(self.root.iterdir()):
                try:
                    archive = run / "archive"
                    if run.is_dir() and archive.is_dir():
                        files.extend(sorted(p for p in archive.iterdir() if p.suffix == ".rec" and p.is_file()))
                except OSError as error:
                    warnings.append({"file": str(run), "type": "unreadable", "error": str(error)})
            manifest = []
            for path in files:
                try:
                    stat = path.stat()
                except OSError as error:
                    warnings.append({"file": str(path), "type": "unreadable", "error": str(error)})
                    continue
                meta = path.parent.parent / "run.meta"
                try:
                    raw_meta = meta.read_text(errors="replace") if meta.exists() else ""
                except OSError as error:
                    warnings.append({"file": str(meta), "type": "unreadable", "error": str(error)})
                    raw_meta = ""
                manifest.append((str(path), stat.st_size, stat.st_mtime_ns, raw_meta))
            signature = hashlib.sha256(json.dumps([PARSER_VERSION, manifest, warnings]).encode()).hexdigest()
            if self.path.exists():
                try:
                    with sqlite3.connect(self.path) as db:
                        old = json.loads(db.execute("SELECT value FROM metadata").fetchone()[0])
                    if old["generation"] == signature:
                        self.state = old
                        return
                except (sqlite3.Error, ValueError, TypeError, KeyError):
                    pass  # A cache is disposable; the original archive is not.
            import tempfile
            fd, temporary = tempfile.mkstemp(dir=self.cache, suffix=".sqlite")
            os.close(fd)
            with sqlite3.connect(temporary) as db:
                db.executescript("""
                    CREATE TABLE ticks(id INTEGER PRIMARY KEY,day TEXT,source TEXT,
                    contract TEXT,session TEXT,sequence TEXT,received INTEGER,price REAL,
                    file TEXT,offset INTEGER,batch TEXT,digest TEXT,UNIQUE(source,session,sequence));
                    CREATE TABLE metadata(value TEXT);
                    CREATE TABLE batches(id TEXT PRIMARY KEY,meta TEXT);
                """)
                for filename, size, mtime, raw_meta in manifest:
                    path = Path(filename)
                    meta = dict(line.split("=", 1) for line in raw_meta.splitlines() if "=" in line)
                    batch = path.parent.parent.name
                    public_meta = {key: meta.get(key, "unknown") for key in
                                   ("git_tag", "git_commit", "git_dirty", "started_at_utc")}
                    db.execute("INSERT OR IGNORE INTO batches VALUES (?,?)", (batch, json.dumps(public_meta)))
                    invalid_times = 0
                    try:
                        for q in quotes(path, snapshot_size=size, include_digest=True):
                            # Avoid float rounding the last nanosecond into tomorrow.
                            day = day_of(q["received"] - q["received"] % 1_000_000_000)
                            if not day or not 0 < q["received"] <= 9223372036854775807:
                                invalid_times += 1
                                continue
                            source = q["source"] or meta.get("source", "unknown")
                            if source == "multi":
                                source = "unknown"
                            db.execute("INSERT OR IGNORE INTO ticks VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?)",
                                       (day, source, q["contract"], q["session"], str(q["sequence"]),
                                        q["received"], q["price"], filename, q["frame_offset"], batch, q["digest"]))
                        after = path.stat()
                        if (after.st_size, after.st_mtime_ns) != (size, mtime):
                            raise ValueError("归档在索引期间发生变化，请刷新重新加载")
                    except (OSError, ValueError, struct.error) as error:
                        text = str(error)
                        category = ("unreadable" if isinstance(error, OSError) else
                                    "unsupported" if "unsupported" in text else
                                    "unfinished_tail" if "incomplete" in text else "corrupt_or_changed")
                        warnings.append({"file": filename, "type": category, "error": text})
                    if invalid_times:
                        warnings.append({"file": filename, "type": "invalid_receive_time", "count": invalid_times})
                db.execute("CREATE INDEX selection ON ticks(day,source,contract,received,id)")
                db.execute("CREATE TABLE catalog AS SELECT day,source,contract,count(*) AS n,min(received) AS start,max(received) AS end FROM ticks GROUP BY day,source,contract")
                state = {"status": "ready", "generation": signature,
                         "loaded_at": datetime.now(timezone.utc).isoformat(),
                         "warnings": warnings, "incomplete": bool(warnings)}
                db.execute("INSERT INTO metadata VALUES(?)", (json.dumps(state),))
            os.replace(temporary, self.path)
            temporary = None
            self.state = state
        except Exception as error:
            self.state = {"status": "error", "error": str(error)}
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)

    def query(self, kind, params):
        if self.state["status"] == "idle":
            self.refresh()
        state = dict(self.state)
        if state["status"] != "ready":
            return state
        return self._query(kind, params)

    def _query(self, kind, params, connection=None):
        with (nullcontext(connection) if connection is not None else
              sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            # Database and metadata are read from the same published snapshot.
            state = json.loads(db.execute("SELECT value FROM metadata").fetchone()[0])
            days = [r[0] for r in db.execute("SELECT DISTINCT day FROM catalog ORDER BY day DESC")]
            day = params.get("date") or (days[0] if days else "")
            if day:
                date.fromisoformat(day)
            sources = [r[0] for r in db.execute("SELECT DISTINCT source FROM catalog WHERE day=? ORDER BY source", (day,))]
            source = params.get("source") or (sources[0] if sources else "")
            contracts = [r[0] for r in db.execute("SELECT contract FROM catalog WHERE day=? AND source=? ORDER BY contract", (day, source))]
            contract = params.get("contract") or (contracts[0] if contracts else "")
            args = (day, source, contract)
            where = "day=? AND source=? AND contract=?"
            bounds = db.execute(f"SELECT n,start,end FROM catalog WHERE {where}", args).fetchone()
            count, start, end = bounds if bounds else (0, None, None)
            result = {**state, "dates": days, "date": day, "sources": sources,
                      "source": source, "contracts": contracts, "contract": contract,
                      "count": count, "start": str(start or 0), "end": str(end or 0)}
            if kind == "catalog":
                result["contract_bounds"] = {
                    r[0]: {"count": r[1], "start": str(r[2]), "end": str(r[3])}
                    for r in db.execute("SELECT contract,n,start,end FROM catalog WHERE day=? AND source=? ORDER BY contract", (day, source))
                }
                result["details"] = [dict(id=r[0], metadata=json.loads(r[1])) for r in db.execute(
                    f"SELECT id,meta FROM batches WHERE id IN (SELECT DISTINCT batch FROM ticks WHERE {where})", args)]
                return result
            if not count:
                return result
            position = str(params.get("time") or start).split(":")
            if len(position) > 2:
                raise ValueError("无效回放位置")
            target = int(position[0])
            cursor_id = int(position[1]) if len(position) == 2 else None
            if not 0 <= target <= 9223372036854775807:
                raise ValueError("无效回放时间")
            if kind == "curve":
                # Two extrema per time bucket, in true receive order, bounded output.
                span = max(1, (min(target, end) - start + 1) // 600)
                points, bucket, extrema, first, last = [], None, [], None, None
                for row in db.execute(f"SELECT received,price,id FROM ticks WHERE {where} AND received<=? ORDER BY received,id", (*args, target)):
                    group = (row[0] - start) // span
                    if group != bucket and extrema:
                        points.extend(sorted({tuple(r) for r in extrema}, key=lambda r: (r[0], r[2])))
                        extrema = []
                    bucket = group
                    if row[1] is not None and math.isfinite(row[1]) and abs(row[1]) < 1e300:
                        if first is None:
                            first = tuple(row)
                        last = tuple(row)
                        extrema = [min(extrema + [row], key=lambda r: r[1]), max(extrema + [row], key=lambda r: r[1])]
                points.extend(sorted({tuple(r) for r in extrema}, key=lambda r: (r[0], r[2])))
                if first is not None:
                    points.extend([first, last])
                result["points"] = [[str(r[0]), r[1]] for r in sorted(set(points), key=lambda r: (r[0], r[2]))]
                return result
            if not params.get("time") or cursor_id == 0:
                row = db.execute(f"SELECT * FROM ticks WHERE {where} ORDER BY received,id LIMIT 1", args).fetchone()
            elif cursor_id is not None:
                row = db.execute(f"SELECT * FROM ticks WHERE {where} AND (received,id)<=(?,?) ORDER BY received DESC,id DESC LIMIT 1", (*args, target, cursor_id)).fetchone()
            else:
                row = db.execute(f"SELECT * FROM ticks WHERE {where} AND received<=? ORDER BY received DESC,id DESC LIMIT 1", (*args, target)).fetchone()
            if row is None:
                return result
            with open(row["file"], "rb") as stream:
                stream.seek(row["offset"])
                header = stream.read(32)
                length = struct.unpack_from("<i", header)[0]
                if not 40 <= length <= 65536:
                    raise ValueError("归档记录已改变，请刷新")
                payload = stream.read(length - 32)
            if hashlib.sha256(payload).hexdigest() != row["digest"]:
                raise ValueError("归档内容已改变，请刷新索引")
            block, template, schema, version = struct.unpack_from("<HHHH", payload)
            if (schema, template, version, block) not in ((701, 1, 2, 194), (701, 1, 3, 324)):
                raise ValueError("不支持的SBE格式")
            q = decode_market_quote(payload)
            if (q["LocalReceiveNs"], q["AeronSessionID"], str(q["AeronSequence"]), q["Contract"]) != (row["received"], row["session"], row["sequence"], row["contract"]):
                raise ValueError("归档记录已改变，请刷新")
            def number(value):
                return value if value is not None and math.isfinite(value) and abs(value) < 1e300 else None
            depth = []
            for level in range(1, 6):
                depth.append([number(q[f"{side}{field}{level}"]) if level <= q["DepthLevels"] else None
                              for side, field in (("Bid", "Price"), ("Bid", "Volume"), ("Ask", "Price"), ("Ask", "Volume"))])
                for column in (0, 2):
                    if depth[-1][column] is None or (depth[-1][column] == 0 and depth[-1][column+1] == 0):
                        depth[-1][column:column+2] = [None, None]
            nxt = db.execute(f"SELECT received,id FROM ticks WHERE {where} AND (received,id)>(?,?) ORDER BY received,id LIMIT 1", (*args, row["received"], row["id"])).fetchone()
            result.update(quote={"price": number(q["LastPrice"]), "depth": depth,
                                 "received": str(row["received"]), "receive_time": receive_iso(row["received"]),
                                 "market_time": q["Datetime"], "sequence": row["sequence"],
                                 "batch": row["batch"], "offset": row["offset"],
                                 "position": f'{row["received"]}:{row["id"]}'},
                          next=str(nxt[0]) if nxt else None,
                          next_position=f"{nxt[0]}:{nxt[1]}" if nxt else None)
            return result

    def snapshots(self, params):
        """One database generation and one target instant for the complete selection."""
        if not isinstance(params, dict) or set(params) - {"date", "source", "contracts", "time"}:
            raise ValueError("不支持的批量回放参数")
        day, source, contracts = params.get("date"), params.get("source"), params.get("contracts")
        if not isinstance(day, str) or not isinstance(source, str) or not source:
            raise ValueError("需要日期和行情源")
        date.fromisoformat(day)
        if not isinstance(contracts, list) or any(not isinstance(c, str) or not c for c in contracts):
            raise ValueError("contracts 必须为合约名称列表")
        stamp = params.get("time")
        if not isinstance(stamp, str) or not stamp.isascii() or not stamp.isdigit():
            raise ValueError("回放时间必须为纳秒整数字符串")
        target = int(stamp)
        if not 0 <= target <= 9223372036854775807:
            raise ValueError("无效回放时间")
        if self.state["status"] == "idle":
            self.refresh()
        if self.state["status"] != "ready":
            return dict(self.state)
        with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            state = json.loads(db.execute("SELECT value FROM metadata").fetchone()[0])
            items = []
            for contract in dict.fromkeys(contracts):
                item = {"contract": contract, "quote": None, "next": None}
                try:
                    result = self._query("snapshot", {
                        "date": day, "source": source, "contract": contract, "time": stamp,
                    }, db)
                    item.update({k: result.get(k) for k in ("count", "start", "end", "quote", "next", "next_position")})
                    if not result["count"]:
                        item["state"] = "unavailable"
                    elif target < int(result["start"]):
                        item.update(state="before_start", next=result["start"])
                    else:
                        item["state"] = "ended" if target >= int(result["end"]) else "active"
                    if item["quote"]:
                        evidence = db.execute("SELECT meta FROM batches WHERE id=?", (item["quote"]["batch"],)).fetchone()
                        item["quote"]["batch_metadata"] = json.loads(evidence[0]) if evidence else {}
                except (OSError, ValueError, sqlite3.Error, struct.error) as error:
                    item.update(state="error", error=str(error))
                items.append(item)
            return {**state, "date": day, "source": source, "time": stamp, "items": items}
