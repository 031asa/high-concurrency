import json
import struct
import time
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

from dashboard.replay import ReplayStore
from ydcore.archive_analysis import CHINA, quotes


def ns(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=CHINA).timestamp()) * 10**9


def frame(seq, received, source="live", contract="IF2609", version=3, price=123.5):
    size = 324 if version == 3 else 194
    block = bytearray(size)
    struct.pack_into("<QQQdddqqBB", block, 0, seq, received - 1000, received,
                     price, price - 1, price + 1, 4, 7, 1, 1)
    strings = ["session", contract, "20990101", "09:00:00.000"]
    if version == 3:
        strings += ["CFFEX", "20260909", "09:00:00", source]
    body = struct.pack("<HHHH", size, 1, 701, version) + block
    for item in strings:
        value = item.encode()
        body += struct.pack("<H", len(value)) + value
    length = len(body) + 32
    header = bytearray(32)
    struct.pack_into("<iBBH", header, 0, length, 0, 192, 1)
    return header + body + bytes((-length) % 32)


def run(root, name, data):
    archive = root / name / "archive"
    archive.mkdir(parents=True)
    (archive.parent / "run.meta").write_text("source=legacy\nversion_tag=v0.9.1\n")
    path = archive / "0-0.rec"
    path.write_bytes(data)
    return path


def ready(store):
    store.refresh()
    deadline = time.monotonic() + 30
    while store.state["status"] == "loading" and time.monotonic() < deadline:
        time.sleep(.01)
    assert store.state["status"] == "ready", store.state


def test_catalog_date_merge_dedup_depth(tmp_path):
    root = tmp_path / "raw"
    t = ns("2026-09-09T23:59:59")
    run(root, "not-a-date", frame(1, t) + frame(2, t + 2*10**9))
    run(root, "batch-b", frame(1, t) + frame(3, t, "tts", "rb2610", price=999))
    store = ReplayStore(root, tmp_path / "cache")
    ready(store)
    catalog = store.query("catalog", {})
    assert catalog["dates"] == ["2026-09-10", "2026-09-09"]
    assert catalog["count"] == 1
    params = {"date": "2026-09-09", "source": "live", "contract": "IF2609"}
    q = store.query("snapshot", params)
    assert q["count"] == 1
    assert q["quote"]["price"] == 123.5
    assert q["quote"]["depth"][0] == [122.5, 4, 124.5, 7]
    assert q["quote"]["depth"][1] == [None]*4
    assert q["quote"]["receive_time"].startswith("2026-09-09T23:59:59")
    assert store.query("snapshot", {**params, "source": "tts", "contract": "rb2610"})["quote"]["price"] == 999
    assert store.query("catalog", {"date": "2026-01-01"})["count"] == 0


def test_legacy_cache_append_and_mutation(tmp_path):
    t = ns("2026-09-09T09:00:00")
    path = run(tmp_path / "raw", "a", frame(1, t, version=2))
    store = ReplayStore(tmp_path / "raw", tmp_path / "cache")
    ready(store)
    assert store.query("snapshot", {})["source"] == "legacy"
    signature = store.state["generation"]
    ready(store)
    assert store.state["generation"] == signature
    with path.open("ab") as stream:
        stream.write(frame(2, t+10**9, version=2))
    assert store.query("catalog", {})["count"] == 1
    assert store.query("snapshot", {})["quote"]["price"] == 123.5
    ready(store)
    assert store.query("catalog", {})["count"] == 2
    assert store.state["generation"] != signature
    data = bytearray(path.read_bytes())
    struct.pack_into("<d", data, 40+24, 567)
    path.write_bytes(data)
    with pytest.raises(ValueError, match="改变"):
        store.query("snapshot", {})


def test_incomplete_unknown_and_independent_clients(tmp_path):
    t = ns("2026-09-09T09:00:00")
    run(tmp_path / "raw", "a", frame(1, t) + frame(2, t+90*10**9, price=888) + b"bad")
    run(tmp_path / "raw", "b", frame(9, t, version=4))
    store = ReplayStore(tmp_path / "raw", tmp_path / "cache")
    ready(store)
    assert store.state["incomplete"]
    assert len(store.state["warnings"]) == 2
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda stamp: store.query("snapshot", {"time": str(stamp)}), [t,t+90*10**9]*4))
    assert [r["quote"]["price"] for r in results] == [123.5,888]*4
    assert results[0]["next"] == str(t+90*10**9)
    assert results[1]["next"] is None
    assert store.query("curve", {"time": str(t)})["points"] == [[str(t),123.5]]


def test_curve_large_and_real_locator(tmp_path):
    t = ns("2026-09-09T09:00:00")
    path = run(tmp_path / "raw", "a", b"".join(frame(i,t+i*10**6,price=i%17) for i in range(5000)))
    store = ReplayStore(tmp_path / "raw", tmp_path / "cache")
    ready(store)
    points = store.query("curve", {"time": str(t+5000*10**6)})["points"]
    assert len(points) <= 1204
    assert min(p[1] for p in points)==0 and max(p[1] for p in points)==16
    for record in list(quotes(path))[::499]:
        q = store.query("snapshot", {"time": str(record["received"])})["quote"]
        assert q["offset"]==record["frame_offset"] and q["price"]==record["price"]


def test_same_time_stable_cursor(tmp_path):
    t = ns("2026-09-09T09:00:00")
    run(tmp_path / "raw", "a", frame(1,t,price=1)+frame(2,t,price=2)+frame(3,t+1,price=3))
    store = ReplayStore(tmp_path / "raw", tmp_path / "cache")
    ready(store)
    first = store.query("snapshot", {})
    assert first["quote"]["price"]==1
    second = store.query("snapshot", {"time":first["next_position"]})
    assert second["quote"]["price"]==2
    third = store.query("snapshot", {"time":second["next_position"]})
    assert third["quote"]["price"]==3 and third["next"] is None


def test_last_nanosecond_of_day(tmp_path):
    midnight = ns("2026-09-10T00:00:00")
    run(tmp_path / "raw", "a", frame(1,midnight-1)+frame(2,midnight))
    store = ReplayStore(tmp_path / "raw", tmp_path / "cache")
    ready(store)
    assert store.query("catalog", {"date":"2026-09-09"})["count"]==1
    assert store.query("catalog", {"date":"2026-09-10"})["count"]==1


def test_failed_rebuild_retains_cache(tmp_path, monkeypatch):
    root=tmp_path/"raw"
    run(root,"a",frame(1,ns("2026-09-09T09:00:00")))
    store=ReplayStore(root,tmp_path/"cache")
    ready(store)
    previous=store.path.read_bytes()
    run(root,"b",frame(2,ns("2026-09-09T09:00:01")))
    def fail(*args):
        raise OSError("simulated publication failure")
    monkeypatch.setattr("dashboard.replay.os.replace",fail)
    store._build()
    assert store.state["status"]=="error"
    assert store.path.read_bytes()==previous
    assert list(store.cache.glob("*.sqlite"))==[store.path]


def test_http_loading_does_not_block_live(tmp_path, monkeypatch):
    import threading
    from urllib.request import urlopen
    from urllib.error import HTTPError
    from http.server import ThreadingHTTPServer
    from dashboard.server import DashboardHandler
    gate=threading.Event()
    store=ReplayStore(tmp_path/"raw",tmp_path/"cache")
    monkeypatch.setattr(store,"_build",lambda:gate.wait(5))
    class Handler(DashboardHandler):
        replay_store=store
        result_root=tmp_path/"raw"
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    root=f"http://127.0.0.1:{server.server_port}"
    try:
        assert json.load(urlopen(root+"/api/replay/catalog",timeout=2))["status"]=="loading"
        assert json.load(urlopen(root+"/api/status",timeout=2))["dashboard_status"]=="IDLE"
        script = urlopen(root+"/replay.js", timeout=2)
        assert script.headers.get_content_type() == "text/javascript"
        assert b"class ReplayWorkspace" in script.read()
        assert b"replay-template" in urlopen(root+"/", timeout=2).read()
        with pytest.raises(HTTPError) as error:
            urlopen(root+"/api/replay/catalog?path=/etc/passwd")
        assert error.value.code==400
    finally:
        gate.set()
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(not os.environ.get("YD_REPLAY_ACCEPTANCE_CACHE"),reason="explicit real archive cache required")
def test_real_archive_acceptance():
    cache=Path(os.environ["YD_REPLAY_ACCEPTANCE_CACHE"])
    root=Path(os.environ["YD_REPLAY_ACCEPTANCE_ROOT"])
    store=ReplayStore(root,cache)
    with sqlite3.connect(store.path) as db:
        db.row_factory=sqlite3.Row
        store.state=json.loads(db.execute("SELECT value FROM metadata").fetchone()[0])
        rows=list(db.execute("SELECT * FROM ticks ORDER BY received,id LIMIT 1"))
        rows+=list(db.execute("SELECT * FROM ticks ORDER BY received DESC,id DESC LIMIT 1"))
        rows+=list(db.execute("SELECT * FROM ticks ORDER BY random() LIMIT 30"))
        total=db.execute("SELECT count(*) FROM ticks").fetchone()[0]
        for row in rows:
            q=store.query("snapshot",{"date":row["day"],"source":row["source"],"contract":row["contract"],"time":f'{row["received"]}:{row["id"]}'})["quote"]
            with open(row["file"],"rb") as stream:
                stream.seek(row["offset"]+40)
                fixed=stream.read(194)
            seq,market,received,price=struct.unpack_from("<QQQd",fixed)
            assert q["received"]==str(received) and q["sequence"]==str(seq)
            assert q["price"]==price
            assert datetime.fromtimestamp(received//10**9,CHINA).date().isoformat()==row["day"]
            for level in range(5):
                offset=32 if level==0 else 66+(level-1)*32
                bid,ask,bv,av=struct.unpack_from("<ddqq",fixed,offset)
                expected=[bid,bv,ask,av] if level<fixed[65] else [None]*4
                for col in (0,2):
                    if expected[col]==0 and expected[col+1]==0:
                        expected[col:col+2]=[None,None]
                assert q["depth"][level]==expected
    print(json.dumps({"indexed_records":total,"independently_checked":len(rows),"dates":store.query("catalog",{})["dates"]}))
