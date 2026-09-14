"""Multi-instrument YDApi tests: fake SDK, local UDP only."""
import json
import signal
import socket
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from aeron_mvp import ydapi_bridge as bridge
from aeron_mvp.source_config import load_source_config, SourceConfigError

def document():
    return dict(schema_version=1, name="ydapi-main", kind="ydapi",
                account_config="/secure/account.json", api_config="/secure/ydClient.ini")

@pytest.mark.parametrize("selection,expected", [
    ({"instrument": "IF2609"}, "IF2609"),
    ({"instruments": ["IF2609", "IC2609"]}, "IF2609,IC2609"),
    ({"instruments": ["IF"+str(i) for i in range(200)]}, ",".join("IF"+str(i) for i in range(200))),
])
def test_config_lists(tmp_path, selection, expected):
    path = tmp_path/"source.json"
    path.write_text(json.dumps(dict(document(), **selection)))
    assert load_source_config(path,tmp_path)["instruments"] == expected

@pytest.mark.parametrize("selection", [
    {}, {"instruments":[]}, {"instruments":[""]}, {"instruments":["IF2609","IF2609"]},
    {"instruments":"IF2609,IC2609"}, {"instrument":"IF2609","instruments":["IC2609"]},
])
def test_invalid_config(tmp_path, selection):
    path=tmp_path/"source.json"; path.write_text(json.dumps(dict(document(),**selection)))
    with pytest.raises(SourceConfigError): load_source_config(path,tmp_path)

@pytest.mark.parametrize("args", [
    ["--instruments","IF2609,,IC2609"], ["--instruments","IF2609,IF2609"],
    ["--instrument","IF2609","--instruments","IC2609"],
])
def test_invalid_cli(args):
    with pytest.raises(SystemExit):
        bridge.parse_args(args+["--account-config","unused","--api-config","unused"])

def test_many_callback_filters():
    pub=Mock(sequence=1); listener=bridge.YdApiListener(pub)
    for i in range(200): listener.enable_instrument("IF"+str(i))
    for i in range(200): listener.marketdata(SimpleNamespace(instrument="IF"+str(i)))
    assert pub.publish.call_count==200
    listener.marketdata(SimpleNamespace(instrument="OTHER"))
    assert pub.publish.call_count==200
    listener.disable_instrument()
    listener.marketdata(SimpleNamespace(instrument="IF0"))
    assert pub.publish.call_count==200

@pytest.mark.parametrize("failure", [None, "reject", "raise", "missing"])
def test_one_login_subscription_cleanup_and_wire(monkeypatch, failure):
    selected=["IF2609","IC2609","IH2609"]
    handlers={}; subscriptions=[]; removed=[]; starts=[]; stops=[]; created=[]
    monkeypatch.setattr(bridge.signal,"signal",lambda number,handler:handlers.update({number:handler}))
    monkeypatch.setattr(bridge,"load_account",lambda path:("test-account","test-password"))
    def create(listener,*unused):
        class API:
            def start(self):
                starts.append(1); listener.login(0,0,False); listener.caughtup()
                return True
            def get_instrument(self,name):
                return None if failure=="missing" and name==selected[1] else object()
            def subscribe(self,name):
                subscriptions.append(name)
                if name==selected[1] and failure in ("reject","raise"):
                    if failure=="raise": raise RuntimeError("subscription exception")
                    return False
                listener.marketdata(SimpleNamespace(instrument=name,timestamp="10:00:00.000"))
                if name==selected[-1]: handlers[signal.SIGTERM](signal.SIGTERM,None)
                return True
            def unsubscribe(self,name): removed.append(name)
            def stop(self): stops.append(1)
        created.append(1); return API()
    monkeypatch.setattr(bridge,"create_api",create)
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1",0)); receiver.settimeout(.2)
        args=["--instruments",",".join(selected),"--repeat","1","--udp-port",str(receiver.getsockname()[1]),
              "--account-config","unused","--api-config","unused"]
        if failure:
            with pytest.raises((ValueError,RuntimeError)): bridge.main(args)
        else:
            assert bridge.main(args)==0
            records=[bridge.PACKET.unpack(receiver.recvfrom(65535)[0]) for _ in selected]
            assert [r[4] for r in records]==[1,2,3]
            assert [r[28].split(bytes([0]))[0].decode() for r in records]==selected
    assert len(created)==len(starts)==len(stops)==1
    assert removed == ([] if failure=="missing" else [selected[0]] if failure else selected[::-1])
    if failure=="missing": assert subscriptions==[]
