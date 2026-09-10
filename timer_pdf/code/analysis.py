"""Read-only archive/log analysis. No broker, network or trading imports."""
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, pstdev
from ydcore.archive_analysis import quotes, iso, day_of
from .rules import TZ

LABELS = {
 "invalid_time":"无效时间", "unknown_rule":"规则或交易日历待核验",
 "future_date":"未来日期", "cross_session_review":"跨时段旧记录待核验",
 "cross_date_review":"跨日旧记录待核验", "large_latency":"交易时段大观测差",
 "gap":"交易时段接收缺口待核验", "sequence":"序号校验报错，根因待核验",
 "read_error":"读取或解析失败", "mode_unknown":"来源延迟模式未知",
 "clock_error":"时钟检测失败", "clock_exceeded":"时钟偏移超限",
 "clock_gap":"时钟检测覆盖缺口", "distribution_unknown":"分发证据不可得",
 "version_unknown":"运行版本缺失"
}

def stats(values):
    values=sorted(values); n=len(values)
    out=dict(n=n,mean=mean(values) if n else None,std=pstdev(values) if n else None,
             max=max(values) if n else None)
    out.update({f"p{p}":values[max(0,math.ceil(n*p/100)-1)] if n else None for p in (50,90,95,99)})
    return out

def metadata(path):
    return dict(line.split("=",1) for line in path.read_text().splitlines() if "=" in line)

def identity(meta):
    return dict(tag=meta.get("git_tag","unknown"),commit=meta.get("git_commit","unknown"),dirty=meta.get("git_dirty","unknown"))

def version_label(versions):
    labels=set()
    for v in versions:
        tag,commit=v["tag"],v["commit"]
        if tag not in ("unknown","",None):
            label=tag
        elif commit not in ("unknown","",None):
            label="commit-"+commit[:12]
        else: label="tag-unknown"
        if v["dirty"] in ("true",True): label+="-dirty"
        labels.add(re.sub(r"[^A-Za-z0-9._-]","_",label))
    return next(iter(labels)) if len(labels)==1 else "tag-multi" if labels else "tag-unknown"

def pending(kind,reason,**data):
    return dict(kind=kind,label=LABELS.get(kind,kind),reason=reason,**data)

def quote_evidence(q,run,path):
    return dict(source=q["source"],contract=q["contract"],run=run,session=q["session"],
                sequence=str(q["sequence"]),event=iso(q["market"]),received=iso(q["received"]),
                event_ns=str(q["market"]),receive_ns=str(q["received"]),raw=q["market_raw"],
                price=q["price"],evidence=str(path),offset=q.get("frame_offset",""))

def clock_report(root,day,items):
    records=[]; errors=[]
    for path in sorted((root/day).glob("*.json")):
        try:
            q=json.loads(path.read_text())
            if q.get("date_beijing")!=day or q.get("schema")!=1: raise ValueError("wrong date/schema")
            records.append((q,path))
        except (OSError,ValueError) as e:
            errors.append(str(path)+": "+str(e))
    groups={}
    for q,path in records:
        key=(q.get("hostname","unknown"),q.get("server","unknown"))
        g=groups.setdefault(key,dict(host=key[0],server=key[1],count=0,failed=0,exceeded=0,values=[],times=[]))
        g["count"]+=1
        try: g["times"].append(datetime.fromisoformat(q["started_at_utc"]).astimezone(TZ))
        except (KeyError,ValueError): pass
        value=q.get("offset_ms")
        if q.get("status") in ("PASS","EXCEEDED") and isinstance(value,(int,float)) and math.isfinite(value):
            g["values"].append(value)
            if q["status"]=="EXCEEDED":
                g["exceeded"]+=1
                items.append(pending("clock_exceeded","有效探测超阈值；不扣减行情观测差",received=q.get("started_at_utc"),source=key[0],raw=q.get("raw_output"),evidence=str(path),value=value))
        else:
            g["failed"]+=1
            items.append(pending("clock_error","探测失败不是0偏移",received=q.get("started_at_utc"),source=key[0],raw=q.get("raw_output"),evidence=str(path)))
    for g in groups.values():
        vals=g.pop("values"); times=sorted(g.pop("times"))
        g.update(valid=len(vals),mean=mean(vals) if vals else None,max_abs=max(map(abs,vals)) if vals else None,
                 first=times[0].isoformat() if times else None,last=times[-1].isoformat() if times else None)
        for a,b in zip(times,times[1:]):
            seconds=(b-a).total_seconds()
            if seconds>660:
                items.append(pending("clock_gap","探测间隔超过11分钟，期间偏移不可推断",source=g["host"],event=a.isoformat(),received=b.isoformat(),value=seconds,evidence=str(root/day)))
    if not records:
        items.append(pending("clock_gap","当天无可用时钟留档，不回填其他日期",evidence=str(root/day)))
    for e in errors: items.append(pending("read_error","时钟记录解析失败",evidence=e))
    return dict(groups=list(groups.values()),read_errors=errors)

def analyze(input_root,clock_root,day,rules):
    input_root=Path(input_root); selected=date.fromisoformat(day)
    end=datetime.combine(selected+timedelta(days=1),datetime.min.time(),TZ)
    sources={}; items=[]; runs=[]; errors=[]; seen=set(); duplicates=0; files=0
    if not input_root.is_dir():
        errors.append("输入目录不存在: "+str(input_root))
    for run in sorted(input_root.iterdir() if input_root.is_dir() else []):
        if not run.is_dir() or not (run/"run.meta").is_file(): continue
        try:
            meta=metadata(run/"run.meta")
            start=meta.get("started_at_utc")
            if start and datetime.fromisoformat(start.replace("Z","+00:00"))>=end: continue
        except (ValueError,OSError) as e:
            errors.append(str(run)+": "+str(e)); continue
        counts=Counter(); batch_modes={}; batch_cum=defaultdict(lambda:[0,0.0])
        run_errors=[]; run_seen=set()
        for path in sorted((run/"archive").glob("*.rec")):
            try:
                files+=1
                for q in quotes(path):
                    q["source"] = q["source"] or meta.get("source", "unknown")
                    mode=meta.get("source_latency_mode."+q["source"],meta.get("latency_mode","unknown"))
                    key=(q["source"],q["session"],q["sequence"])
                    # Full-batch cumulative mean is explicitly separate from daily statistics.
                    if key not in run_seen and q["valid"] and day_of(q["market"]) and mode=="live":
                        agg=batch_cum[q["source"]];agg[0]+=1
                        agg[1]+=abs(q["received"]-q["market"])/1e6
                    run_seen.add(key)
                    if day_of(q["received"])!=day: continue
                    if key in seen: duplicates+=1;continue
                    seen.add(key); counts[q["source"]]+=1; batch_modes[q["source"]]=mode
                    s=sources.setdefault(q["source"],dict(count=0,modes=set(),categories=Counter(),all_valid=[],live=[],
                           contracts=defaultdict(list),bins=defaultdict(list),hourly=Counter(),ticks=[],examples=[]))
                    s["count"]+=1;s["modes"].add(mode)
                    received=datetime.fromtimestamp(q["received"]/1e9,TZ)
                    s["hourly"][received.strftime("%H:00")]+=1
                    s["ticks"].append((q["received"],q["contract"],run.name,q["session"],q["sequence"]))
                    if mode=="historical_replay": s["categories"]["historical_replay"]+=1; continue
                    if mode!="live":
                        s["categories"]["mode_unknown"]+=1
                        if s["categories"]["mode_unknown"]==1:
                            items.append(pending("mode_unknown","该源模式非live，不计算实盘延迟",**quote_evidence(q,run.name,path)))
                        continue
                    category,lag=rules.classify(q)
                    s["categories"][category]+=1
                    if lag is not None: s["all_valid"].append(lag)
                    ev=quote_evidence(q,run.name,path)
                    if category=="live":
                        s["live"].append(lag); s["contracts"][q["contract"]].append(lag)
                        event=datetime.fromtimestamp(q["market"]/1e9,TZ)
                        bucket=event.replace(minute=event.minute//15*15,second=0,microsecond=0).isoformat()
                        s["bins"][bucket].append(lag)
                        if lag>rules.reference*1000:
                            items.append(pending("large_latency","保留实时主统计；仅从3分钟参考值排除，根因待核验",value=lag,**ev))
                    elif category!="session_snapshot":
                        items.append(pending(category,"排除实时统计，原始记录保留，需核对时间/规则/初始化状态",value=lag,**ev))
            except (OSError,ValueError,UnicodeError) as e:
                run_errors.append(str(path)+": "+str(e))
        if not counts:
            # Corrupt archives with unknown coverage cannot silently disappear.
            errors.extend(run_errors)
            continue
        errors.extend(run_errors)
        v=identity(meta)
        runs.append(dict(run=run.name,counts=dict(counts),version=v,status=meta.get("status","unknown"),
                         cumulative={k:dict(n=n,mean=total/n if n else None) for k,(n,total) in batch_cum.items()},
                         zmq_endpoint=meta.get("zmq_endpoint",""),evidence=str(run/"run.meta")))
        if v["commit"]=="unknown":
            items.append(pending("version_unknown","历史run.meta无部署版本，不拿当前tag回填",run=run.name,evidence=str(run/"run.meta")))
        if not meta.get("zmq_endpoint"):
            items.append(pending("distribution_unknown","未配置分发endpoint；不是丢包事件，消费证据不可得",run=run.name,evidence=str(run/"run.meta")))
        for name in ("mux.log","publisher.log"):
            path=run/name
            if not path.exists(): continue
            events=set()
            try:
                for no,line in enumerate(path.read_text(errors="replace").splitlines(),1):
                    m=re.search(r"expected[=: ]+(\d+).*actual[=: ]+(\d+)",line)
                    if not m:continue
                    sm=re.search(r"\bsource=(\S+)",line)
                    source=sm[1] if sm else "multi-source"
                    event=(source,*m.groups())
                    if event in events:continue
                    events.add(event)
                    items.append(pending("sequence","校验报错已确认；时间范围为覆盖日的整个批次日志，非独立丢包数",
                           source=source,run=run.name,evidence=str(path)+":"+str(no),raw=line,
                           expected=m[1],actual=m[2],layer=name))
            except OSError as e: errors.append(str(path)+": "+str(e))
    for source,s in sources.items():
        ticks=sorted(s.pop("ticks")); contracts={x[1] for x in ticks}; raw_gaps=0; excluded=0
        for a,b in zip(ticks,ticks[1:]):
            sec=(b[0]-a[0])/1e9
            if sec<=rules.gap or "live" not in s["modes"]: continue
            raw_gaps+=1
            active=rules.active_seconds(contracts,datetime.fromtimestamp(a[0]/1e9,TZ),datetime.fromtimestamp(b[0]/1e9,TZ))
            if active is not None and active<=rules.gap: excluded+=1;continue
            items.append(pending("gap","按当日观测合约时段并集扣休市；缺少逐时订阅清单，不认定断联",
                        source=source,contract=a[1]+" -> "+b[1],run=a[2]+" -> "+b[2],
                        event=iso(a[0]),received=iso(b[0]),session=a[3]+" -> "+b[3],
                        sequence=str(a[4])+" -> "+str(b[4]),value=active,
                        raw="raw_gap_seconds="+str(sec),evidence=str(input_root)+"/<上述批次>/archive/*.rec"))
        live=s.pop("live")
        s.update(first=iso(ticks[0][0]),last=iso(ticks[-1][0]),all_valid=stats(s["all_valid"]),
                 clean=stats(live),reference=stats([x for x in live if x<=rules.reference*1000]),
                 contracts={k:stats(v) for k,v in sorted(s["contracts"].items())},
                 bins={k:stats(v) for k,v in sorted(s["bins"].items())},
                 modes=sorted(s["modes"]),categories=dict(s["categories"]),hourly=dict(sorted(s["hourly"].items())),
                 raw_gaps=raw_gaps,normal_gaps=excluded)
    for e in errors:items.append(pending("read_error","输入不完整；不得把未读取部分当0",evidence=e))
    clock=clock_report(Path(clock_root),day,items)
    incomplete=bool(errors or clock["read_errors"])
    return dict(date=day,rule_version=rules.version,rule_sources=rules.data.get("sources",[]),
                generated=datetime.now(TZ).isoformat(),sources=sources,runs=runs,clock=clock,pending=items,
                errors=errors,duplicates=duplicates,files=files,
                status="INCOMPLETE" if incomplete else "NO_DATA" if not sources else "COMPLETE",
                version_label=version_label([r["version"] for r in runs]))
