"""Generate PDF + pending CSV from archives; never start a market/trading service."""
import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys
import uuid

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from timer_pdf.code.rules import Rules, TZ
from timer_pdf.code.analysis import analyze
from ydcore.version_info import current
from timer_pdf.code.publication import require_published

@contextmanager
def lock(root):
    root.mkdir(parents=True,exist_ok=True)
    with (root/".report.lock").open("a") as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError("Another report job holds the output lock")
        yield

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def atomic_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(data,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)

def completed(output,day):
    path=output/".state"/(day+".json")
    if not path.is_file():return False
    try:
        state=json.loads(path.read_text())
        return state["status"] in ("COMPLETE","NO_DATA") and all(
            (output/p).is_file() and digest(output/p)==h for p,h in state["files"].items())
    except (ValueError,OSError,KeyError):return False

def publish(output,day,report,rules,font,generator):
    from timer_pdf.code.render import pdf_report,csv_report
    # File identity belongs to the report generator; run identities stay in the PDF.
    label=generator["commit"];name=day+"__"+label
    destination=output/name
    # Incomplete attempts never replace a previously valid report.
    if report["status"]=="INCOMPLETE":
        destination=output/".failed"/(name+"__"+datetime.now(TZ).strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:8])
    with tempfile.TemporaryDirectory(prefix=".report-",dir=output) as scratch:
        stage=Path(scratch)/name;stage.mkdir()
        pdf=stage/("行情接入报告_"+name+".pdf");csv=stage/("待核验记录_"+name+".csv")
        csv_report(csv,report["pending"]);pdf_report(pdf,report,rules,font,generator)
        for path in (pdf,csv):
            with path.open("rb") as stream:os.fsync(stream.fileno())
        destination.parent.mkdir(parents=True,exist_ok=True)
        previous=None
        if destination.exists():
            previous=output/".history"/(name+"__"+uuid.uuid4().hex)
            previous.parent.mkdir(parents=True,exist_ok=True)
            os.replace(destination,previous)
        try:os.replace(stage,destination)
        except BaseException:
            if previous:os.replace(previous,destination)
            raise
    if report["status"]!="INCOMPLETE":
        atomic_json(output/".state"/(day+".json"),dict(status=report["status"],rule=rules.version,
          generated=report["generated"],generator=generator,
          files={str(p.relative_to(output)):digest(p) for p in destination.iterdir()}))
    print(json.dumps(dict(date=day,status=report["status"],output=str(destination),sources={
        k:dict(count=v["count"],clean=v["clean"],categories=v["categories"]) for k,v in report["sources"].items()}),
        ensure_ascii=False),flush=True)
    return report["status"]!="INCOMPLETE"

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date",default="yesterday")
    p.add_argument("--input-root",type=Path,default=ROOT/"result/aeron-mvp")
    p.add_argument("--clock-root",type=Path,default=ROOT/"result/time-probes")
    p.add_argument("--output-root",type=Path,default=ROOT/"timer_pdf/pdf")
    p.add_argument("--rules",type=Path,default=ROOT/"timer_pdf/code/rules.json")
    p.add_argument("--font")
    p.add_argument("--catch-up-from",type=date.fromisoformat)
    a=p.parse_args(argv)
    target=datetime.now(TZ).date()-timedelta(days=1) if a.date=="yesterday" else date.fromisoformat(a.date)
    output=a.output_root.resolve();inputs=a.input_root.resolve();clock=a.clock_root.resolve()
    if output==inputs or inputs in output.parents or output==clock or clock in output.parents:
        p.error("Report output must be outside raw archive/clock directories")
    if a.catch_up_from and a.catch_up_from>target:p.error("catch-up start is after target")
    rules=Rules(a.rules)
    from timer_pdf.code.render import choose_font
    font=choose_font(ROOT,a.font);generator=current()
    require_published(ROOT,generator)
    start=a.catch_up_from or target
    ok=True
    with lock(output):
        while start<=target:
            day=start.isoformat()
            if not (a.catch_up_from and completed(output,day)):
                print("REPORT_ANALYZE date="+day,flush=True)
                report=analyze(inputs,clock,day,rules)
                ok=publish(output,day,report,rules,font,generator) and ok
            else:print("REPORT_SKIP verified="+day,flush=True)
            start+=timedelta(days=1)
    return 0 if ok else 1

if __name__=="__main__":
    raise SystemExit(main())
