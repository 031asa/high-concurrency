import importlib.util
import json
from datetime import date,datetime
from pathlib import Path
import subprocess
import sys
import pytest
from timer_pdf.code.rules import Rules,TZ
from timer_pdf.code.analysis import analyze,stats,version_label
from timer_pdf.code import main as report_main
from timer_pdf.code.install_timer import units
from ydcore.version_info import current

ROOT=Path(__file__).parents[1]
@pytest.fixture
def rules():return Rules(ROOT/"timer_pdf/code/rules.json")
def ns(s):return int(datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()*1e9)
def q(c,e,r,valid=True):
    return dict(contract=c,market=ns(e) if e else 0,received=ns(r),valid=valid)

@pytest.mark.parametrize("c,e,r,expected",[
 ("IF2609","2026-09-09T07:03:57","2026-09-09T09:28:19","session_snapshot"),
 ("au2612","2026-09-09T02:30:00.500","2026-09-09T03:00:00","session_snapshot"),
 ("IF2609","2026-09-09T10:00:00","2026-09-09T10:04:00","live"),
 ("IF2609","2026-09-09T15:00:00.500","2026-09-09T15:00:01","live"),
 ("au2612","2026-09-08T23:59:59.900","2026-09-09T00:00:00.100","live"),
 ("rb2701","2026-09-09T23:00:00.500","2026-09-09T23:00:01","live"),
 ("IF2609","2026-09-09T11:30:00","2026-09-09T13:05:00","cross_session_review"),
 ("IF2609","2026-09-10T10:00:00","2026-09-09T10:00:00","future_date"),
 ("IF2609",None,"2026-09-09T10:00:00","invalid_time"),
 ("XX2609","2026-09-09T10:00:00","2026-09-09T10:00:00","unknown_rule"),
 ("IF2709","2027-09-09T10:00:00","2027-09-09T10:00:00","unknown_rule"),
 ("IF2609","2026-09-25T10:00:00","2026-09-25T10:00:00","session_snapshot"),
])
def test_classification(rules,c,e,r,expected):
    assert rules.classify(q(c,e,r))[0]==expected

def test_night_and_breaks(rules):
    assert rules.slot("au2612",datetime.fromisoformat("2026-09-26T01:00:00+08:00"))[0] is None
    assert rules.slot("cu2610",datetime.fromisoformat("2026-09-09T01:30:00+08:00"))[0] is None
    assert rules.slot("au2612",datetime.fromisoformat("2026-09-12T01:30:00+08:00"))[0] is not None
    assert rules.slot("au2612",datetime.fromisoformat("2026-09-14T01:30:00+08:00"))[0] is None
    def active(a,b):return rules.active_seconds(["IF2609"],datetime.fromisoformat(a),datetime.fromisoformat(b))
    assert active("2026-09-09T11:30:01+08:00","2026-09-09T13:00:00+08:00")==0
    assert active("2026-09-09T11:30:01+08:00","2026-09-09T13:02:00+08:00")==120

def test_stats():
    s=stats([10,20,30,40]);assert s["mean"]==25
    assert s["std"]==pytest.approx(11.1803398875);assert s["p50"]==20
    assert stats([])["mean"] is None

def test_identity(tmp_path):
    assert current(tmp_path)["commit"] is None
    (tmp_path/"build-version.json").write_text(json.dumps(dict(tag="v0.8.0",commit="abc",dirty=False)))
    assert current(tmp_path)["tag"]=="v0.8.0"
    assert version_label([dict(tag="unknown",commit="unknown",dirty="unknown")])=="unknown"
    assert version_label([dict(tag="v1",commit="a"*40,dirty="true")])=="a"*40
    assert version_label([dict(tag="v1",commit="a"*40,dirty=False),dict(tag="v2",commit="b"*40,dirty=False)])=="a"*40+" / "+"b"*40

def test_archive_reuse(tmp_path,rules):
    spec=importlib.util.spec_from_file_location("archive_fixture",ROOT/"tests/test_archive_analysis.py")
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    r=ns("2026-09-09T10:00:00")
    run=fixture.make_run(tmp_path,[fixture.frame(1,r,r-10_000_000),fixture.frame(2,r,r-30_000_000),
       fixture.frame(3,r,ns("2026-09-09T07:00:00")),fixture.frame(4,r,0,valid=False),
       fixture.frame(5,r,r-10_000_000,"tts"),fixture.frame(1,r,r-10_000_000)])
    data=analyze(tmp_path,tmp_path/"clock","2026-09-09",rules)
    s=data["sources"]["live"]
    assert s["count"]==4 and s["clean"]["n"]==2
    assert s["clean"]["mean"]==20 and s["categories"]["session_snapshot"]==1
    assert data["duplicates"]==1
    assert data["sources"]["tts"]["clean"]["n"]==0
    assert data["version_label"]=="unknown"

def test_lock(tmp_path):
    with report_main.lock(tmp_path):
        with pytest.raises(RuntimeError):
            with report_main.lock(tmp_path):pass

def test_atomic_and_failed_output(tmp_path,rules,monkeypatch):
    from timer_pdf.code import render
    monkeypatch.setattr(render,"pdf_report",lambda path,*args:path.write_bytes(b"pdf"))
    monkeypatch.setattr(render,"csv_report",lambda path,*args:path.write_text("csv"))
    data=dict(version_label="tag-unknown",status="COMPLETE",pending=[],generated="now",sources={})
    generator=dict(commit="a"*40)
    assert report_main.publish(tmp_path,"2026-09-09",data,rules,"unused",generator)
    assert report_main.completed(tmp_path,"2026-09-09")
    destination=tmp_path/("2026-09-09__"+"a"*40)
    before=destination.stat().st_ino
    data["status"]="INCOMPLETE"
    assert not report_main.publish(tmp_path,"2026-09-09",data,rules,"unused",generator)
    assert destination.stat().st_ino==before
    def broken(*args):raise RuntimeError("render failed")
    monkeypatch.setattr(render,"pdf_report",broken)
    with pytest.raises(RuntimeError):report_main.publish(tmp_path,"2026-09-09",data,rules,"unused",generator)
    assert report_main.completed(tmp_path,"2026-09-09")

def test_timer_and_lazy_help():
    s,t=units(["/path with spaces/python","/root/main.py","--catch-up-from","2026-09-09"])
    assert '"/path with spaces/python"' in s and "Persistent=true" in t and "Asia/Shanghai" in t
    code="import sys; import timer_pdf.code.main; assert not any(k.startswith(('ydcore.trading', 'redis')) for k in sys.modules); from ydcore.launcher import main; main(['daily-report','--help'])"
    p=subprocess.run([sys.executable,"-B","-c",code],capture_output=True,text=True,cwd=ROOT)
    assert p.returncode==0 and "Redis" not in p.stderr

def test_parse_failure(tmp_path,rules):
    run=tmp_path/"bad";(run/"archive").mkdir(parents=True)
    (run/"run.meta").write_text("started_at_utc=2026-09-09T00:00:00+00:00")
    (run/"archive/0-0.rec").write_bytes(bytes([1])*80)
    data=analyze(tmp_path,tmp_path/"clock","2026-09-09",rules)
    assert data["status"]=="INCOMPLETE" and data["errors"]

def test_csv_safe(tmp_path):
    from timer_pdf.code.render import csv_report
    import csv
    out=tmp_path/"pending.csv";csv_report(out,[dict(raw="=1+1",value=-2)])
    rows=list(csv.reader(out.open(encoding="utf-8-sig")))
    assert rows[1][-1]=="'=1+1"

def test_startup_version_snapshot(tmp_path):
    subprocess.run(["git","init",str(tmp_path)],check=True,capture_output=True)
    subprocess.run(["git","-C",str(tmp_path),"config","user.email","test@example.invalid"],check=True)
    subprocess.run(["git","-C",str(tmp_path),"config","user.name","Test"],check=True)
    (tmp_path/"a").write_text("a")
    subprocess.run(["git","-C",str(tmp_path),"add","a"],check=True)
    subprocess.run(["git","-C",str(tmp_path),"commit","-m","initial"],check=True,capture_output=True)
    subprocess.run(["git","-C",str(tmp_path),"tag","v1"],check=True)
    before=current(tmp_path);assert before["tag"]=="v1" and before["dirty"] is False
    (tmp_path/"a").write_text("b")
    assert current(tmp_path)["dirty"] is True and before["dirty"] is False
