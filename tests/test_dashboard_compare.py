import json
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from dashboard.replay import ReplayStore
from dashboard.server import DashboardHandler
from test_dashboard_replay import frame, ns, ready, run


def setup_store(tmp_path):
    t = ns("2026-09-11T09:00:00")
    root = tmp_path / "raw"
    run(root, "a", frame(1,t,contract="A",price=100) +
        frame(2,t+10**9,contract="A",price=101) +
        frame(3,t+10**9,contract="A",price=102))
    run(root, "b", frame(4,t+2*10**9,contract="B",price=200) +
        frame(5,t+20*10**9,contract="B",price=201))
    store = ReplayStore(root, tmp_path / "cache")
    ready(store)
    return store, t


def batch(store, t, contracts=("A", "B")):
    return store.snapshots(dict(date="2026-09-11",source="live",contracts=list(contracts),time=str(t)))


def test_shared_time_before_start_end_duplicates_and_empty(tmp_path):
    store,t = setup_store(tmp_path)
    catalog = store.query("catalog",{})
    assert set(catalog["contract_bounds"]) == {"A","B"}
    result = batch(store,t+10**9)
    a,b = result["items"]
    assert a["quote"]["price"] == 102 and a["quote"]["sequence"] == "3"
    assert a["state"] == "ended" and a["next"] is None
    assert b["state"] == "before_start" and b["quote"] is None
    assert b["next"] == str(t+2*10**9)
    result = batch(store,t+3*10**9)
    assert [q["quote"]["price"] for q in result["items"]] == [102,200]
    assert result["items"][1]["next"] == str(t+20*10**9)
    assert all(q["state"]=="ended" for q in batch(store,t+100*10**9)["items"])
    assert batch(store,t,())["items"] == []
    assert len(batch(store,t,("A","A"))["items"]) == 1
    assert batch(store,t,("unknown",))["items"][0]["state"] == "unavailable"
    assert batch(store,t-1,("A",))["items"][0]["quote"] is None


def test_single_corrupt_contract_isolated(tmp_path):
    store,t = setup_store(tmp_path)
    path = tmp_path/"raw"/"a"/"archive"/"0-0.rec"
    data = bytearray(path.read_bytes()); data[70] ^= 1; path.write_bytes(data)
    result = batch(store,t+2*10**9)
    assert result["status"] == "ready"
    # Latest A record remains valid; corrupt the selected first record explicitly.
    result = batch(store,t)
    assert result["items"][0]["state"] == "error"
    assert result["items"][1]["state"] == "before_start"
    result = batch(store,t+2*10**9,("B",))
    assert result["items"][0]["quote"]["price"] == 200


def test_one_generation_even_when_index_replaced_mid_batch(tmp_path, monkeypatch):
    store,t = setup_store(tmp_path)
    old_generation = store.state["generation"]
    query = store._query
    calls = 0
    def replacing(kind,params,connection=None):
        nonlocal calls
        result = query(kind,params,connection)
        calls += 1
        if calls == 1:
            run(tmp_path/"raw","new",frame(6,t,contract="B",price=999))
            store._build()
        return result
    monkeypatch.setattr(store,"_query",replacing)
    result = batch(store,t)
    assert result["generation"] == old_generation
    assert result["items"][1]["state"] == "before_start"
    assert store.state["generation"] != old_generation
    assert batch(store,t)["items"][1]["quote"]["price"] == 999


def test_64_contracts_match_individual_archive_reads(tmp_path):
    t=ns("2026-09-11T09:00:00")
    names=[f"C{i:02}" for i in range(64)]
    data=b"".join(frame(i*2+1,t+i*10**9,contract=c,price=100+i)+
                  frame(i*2+2,t+(100+i)*10**9,contract=c,price=200+i)
                  for i,c in enumerate(names))
    run(tmp_path/"raw","many",data)
    store=ReplayStore(tmp_path/"raw",tmp_path/"cache");ready(store)
    result=batch(store,t+500*10**9,names)
    assert len(result["items"]) == 64
    for i,item in enumerate(result["items"]):
        assert item["contract"] == names[i]
        assert item["quote"]["price"] == 200+i
        assert item["quote"]["depth"][0] == [199+i,4,201+i,7]
        assert item["state"] == "ended"
        assert item["quote"]["received"] == str(t+(100+i)*10**9)


@pytest.mark.parametrize("change",[
    {"contracts":"A"},{"contracts":[1]},{"time":"-1"},{"time":1},{"time":"1:0"},
    {"time":"9223372036854775808"},{"date":"bad"},{"path":"/etc/passwd"},
])
def test_validation(tmp_path,change):
    store,t=setup_store(tmp_path)
    params=dict(date="2026-09-11",source="live",contracts=["A"],time=str(t))
    with pytest.raises(ValueError):store.snapshots({**params,**change})


def test_http_batch_and_legacy_remain_read_only(tmp_path):
    store,t=setup_store(tmp_path)
    class Handler(DashboardHandler):
        replay_store=store
        result_root=tmp_path/"raw"
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    root=f"http://127.0.0.1:{server.server_port}"
    try:
        request=Request(root+"/api/replay/snapshots",data=json.dumps(dict(
            date="2026-09-11",source="live",contracts=["A","B"],time=str(t))).encode(),
            headers={"Content-Type":"application/json"})
        assert len(json.load(urlopen(request))["items"])==2
        assert json.load(urlopen(root+"/api/replay/snapshot"))["quote"]["price"]==100
        assert json.load(urlopen(root+"/healthz"))["status"]=="ok"
        with pytest.raises(HTTPError) as error:
            urlopen(Request(root+"/api/replay/snapshots",data=b"[]"))
        assert error.value.code==400
    finally:
        server.shutdown();server.server_close()

