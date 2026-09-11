"""Install a user systemd timer. Does not touch market/trading services."""
import argparse
from datetime import datetime,timedelta
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from timer_pdf.code.rules import TZ

def quote(s):
    return '"'+str(s).replace("%","%%").replace("\\","\\\\").replace('"','\\"')+'"'

def units(command):
    service="[Unit]\nDescription=Read-only daily market PDF report\nAfter=network-online.target\n\n"
    service+="[Service]\nType=oneshot\nExecStart="+" ".join(map(quote,command))+"\n"
    service+="TimeoutStartSec=infinity\nNoNewPrivileges=true\nUMask=0027\n"
    timer="[Unit]\nDescription=Yesterday market report at 09:00 Beijing\n\n[Timer]\n"
    timer+="OnCalendar=*-*-* 09:00:00 Asia/Shanghai\nPersistent=true\nUnit=ydtrader-daily-report.service\n\n[Install]\nWantedBy=timers.target\n"
    return service,timer

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--since",default=(datetime.now(TZ).date()-timedelta(days=1)).isoformat())
    p.add_argument("--input-root",type=Path,default=ROOT/"result/aeron-mvp")
    p.add_argument("--clock-root",type=Path,default=ROOT/"result/time-probes")
    p.add_argument("--output-root",type=Path,default=ROOT/"timer_pdf/pdf")
    p.add_argument("--rules",type=Path,default=ROOT/"timer_pdf/code/rules.json")
    p.add_argument("--font")
    p.add_argument("--dry-run",action="store_true")
    a=p.parse_args(argv)
    datetime.fromisoformat(a.since)
    command=[sys.executable,"-B",str(ROOT/"timer_pdf/code/main.py"),"--date","yesterday",
       "--catch-up-from",a.since,"--input-root",str(a.input_root.resolve()),"--clock-root",str(a.clock_root.resolve()),
       "--output-root",str(a.output_root.resolve()),"--rules",str(a.rules.resolve())]
    if a.font:command+=["--font",str(Path(a.font).resolve())]
    service,timer=units(command)
    if a.dry_run:print(service+"\n"+timer);return 0
    dest=Path.home()/".config/systemd/user";dest.mkdir(parents=True,exist_ok=True)
    for suffix,text in (("service",service),("timer",timer)):
        (dest/("ydtrader-daily-report."+suffix)).write_text(text)
    subprocess.run(["systemctl","--user","daemon-reload"],check=True)
    subprocess.run(["systemctl","--user","enable","--now","ydtrader-daily-report.timer"],check=True)
    print("Installed; inspect systemctl --user list-timers ydtrader-daily-report.timer")
    return 0
if __name__=="__main__":raise SystemExit(main())
