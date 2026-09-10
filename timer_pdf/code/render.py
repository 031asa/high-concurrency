"""PDF and CSV rendering; independent of dashboard and broker libraries."""
import csv
import os
import sys
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether

def choose_font(root, explicit=None):
    candidates=[explicit,os.environ.get("YD_REPORT_FONT"),
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        str(Path(sys.prefix)/"share/fonts/truetype/wqy/wqy-microhei.ttc"),
        str(root/"result/report-fonts/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")]
    for path in candidates:
        if path and Path(path).is_file():return path
    raise RuntimeError("Chinese font missing: use --font /path/font.ttf or install fonts-wqy-microhei")

def csv_report(path, rows):
    keys=["label","reason","source","contract","run","session","sequence","event","received",
          "event_ns","receive_ns","price","value","expected","actual","layer","evidence","offset","raw"]
    names=["类别","原因与口径","来源","合约","批次","会话","序号","事件或间隔起点","接收或间隔终点",
           "事件纳秒","接收纳秒","最新价","数值_单位见类别","期望序号","实际序号","环节","证据定位","帧偏移","原始字段"]
    def safe(v):
        if v is None:return ""
        if isinstance(v,str) and v.startswith(("=","+","-","@","\t","\r")):return "'"+v
        return v
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f);w.writerow(names)
        for row in rows:w.writerow([safe(row.get(k)) for k in keys])
    with path.open(encoding="utf-8-sig",newline="") as f:
        parsed=list(csv.reader(f))
    assert len(parsed)==len(rows)+1 and all(len(r)==len(keys) for r in parsed)

def pdf_report(path, report, rules, font, generator):
    pdfmetrics.registerFont(TTFont("ReportCN",font,subfontIndex=0))
    navy=colors.HexColor("#173849");teal=colors.HexColor("#007F75")
    styles={k:ParagraphStyle(k,fontName="ReportCN",fontSize=size,leading=lead,textColor=navy,wordWrap="CJK")
            for k,size,lead in (("title",19,26),("h",12,18),("p",10,15),("small",8.5,12))}
    story=[]
    def para(text,style="p"):return Paragraph(escape(str(text)).replace("\n","<br/>"),styles[style])
    def p(text,style="p"):story.extend([para(text,style),Spacer(1,7)])
    def page(text):story.extend([PageBreak()]);p(text,"title")
    def table(rows,widths):
        t=Table([[para(c,"small") for c in row] for row in rows],colWidths=widths,repeatRows=1,hAlign="LEFT")
        t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#DBEDEA")),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#F2F6F7")]),
            ("VALIGN",(0,0),(-1,-1),"TOP"),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6)]))
        story.extend([t,Spacer(1,9)])
    def fmt(v):return f"{v:,.3f}" if isinstance(v,(int,float)) else "不可得"
    p(report["date"]+" 行情接入报告","title")
    p("北京时间接收自然日｜"+report["status"]+"｜生成："+report["generated"],"small")
    p("行情运行版本："+report["version_label"]+"；生成器："+str(generator.get("tag") or generator.get("commit") or "unknown")+
      "；生成器本地修改："+str(generator.get("dirty")),"small")
    p("规则版本："+rules.version+"。历史运行版本只取run.meta，缺失不回填。","small")
    if report["status"]!="COMPLETE":
        p("覆盖说明："+("输入未完整读取，下列仅为已读取样本，不能视为完整日报。" if report["status"]=="INCOMPLETE" else "当天没有可读取行情，不表示零故障。"),"h")
    p("正常盘前、时段切换快照保留归档，不计实时延迟。接收静默及计数差不是已确认丢包；待核验CSV保留全部证据。")
    for source,s in report["sources"].items():
        c=s["clean"];r=s["reference"];a=s["all_valid"]
        p(source+" / "+",".join(s["modes"]),"h")
        rows=[["项目","结果"],["归档 / 实时统计",f"{s['count']:,} / {c['n']:,}条"],
              ["分类",str(s["categories"])],["首末接收",s["first"]+" 至 "+s["last"]]]
        if "live" in s["modes"]:
            rows += [["实时Mean / Std",fmt(c["mean"])+" / "+fmt(c["std"])+" ms"],
                ["实时P50 / P90 / P95 / P99", " / ".join(fmt(c[k]) for k in ("p50","p90","p95","p99"))+" ms"],
                ["实时最大值",fmt(c["max"])+" ms"],
                ["剔除>180秒参考Mean / 样本",fmt(r["mean"])+f" ms / {r['n']:,}条"],
                ["所有时间有效记录总平均",fmt(a["mean"])+f" ms / {a['n']:,}条；含快照，非实时延迟"],
                [">60秒原间隔 / 正常休市移除",f"{s['raw_gaps']} / {s['normal_gaps']}段"]]
        else:rows.append(["延迟","回放或未知模式，不作实时延迟解释"])
        table(rows,[185,320])
    p("统计范围为已读取归档，不包含未持久化的回调。按来源+session+sequence去重；重复重放剔除 "+str(report["duplicates"])+" 条。标准差为总体标准差，分位数为样本nearest-rank；无法识别源端丢包。","small")
    page("交易规则与证据边界")
    rows=[["品种","日盘","夜盘"]]
    for group,spec in rules.data["sessions"].items():
        products=[p for p,g in rules.products.items() if g==group]
        rows.append([" / ".join(products),"; ".join(a+"-"+b for a,b in spec["day"]),"-".join(spec["night"]) if spec["night"] else "无"])
    table(rows,[120,260,125])
    p(f"交易日历适用 {rules.start} 至 {rules.end}，含节假日与节前停夜盘；范围外及未知品种进入待核验。夜盘跨午夜同一时段连续；收盘整秒内的毫秒记录保留。")
    p("事件时段外数据按约定归为非实时快照；不能据此证明柜台原生快照标记。事件处于交易时段、接收稍跨收盘且≤180秒仍计入；交易已开启但仍收到其他时段旧记录时待核验。")
    p("来源级接收缺口按当天观测到的合约时段并集扣除休市；缺少逐时订阅健康记录，因此只作为覆盖待核验。未在归档出现的订阅合约无法靠本报告发现。")
    p("证据单位：大观测差为毫秒；接收/时钟缺口为秒；时钟偏移为毫秒。CSV不把各类型行数相加成故障次数。")
    counts=Counter(x["label"] for x in report["pending"])
    table([["分类","条目数"],*[[k,str(v)] for k,v in counts.items()]],[380,125])
    p("规则参考资料（离线执行，不在生成时联网）：","h")
    for url in report["rule_sources"]:p(url,"small")
    for source,s in report["sources"].items():
        if not s["contracts"]:continue
        page(source+" 分合约与分时统计")
        table([["合约","N","Mean ms","P95 ms","Max ms"],*[[k,str(v["n"]),fmt(v["mean"]),fmt(v["p95"]),fmt(v["max"])] for k,v in s["contracts"].items()]],[90,75,110,110,120])
        p("15分钟事件时间分箱（清洗后实时样本）","h")
        table([["时间箱","N","Mean ms","P95 ms","Max ms"],*[[k,str(v["n"]),fmt(v["mean"]),fmt(v["p95"]),fmt(v["max"])] for k,v in s["bins"].items()]],[165,60,90,90,100])
    page("逐小时归档覆盖")
    for source,s in report["sources"].items():
        p(source,"h")
        table([["接收小时","归档条数"],*[[k,str(v)] for k,v in s["hourly"].items()]],[300,205])
    # Show concrete examples, not invented diagnoses. Each category <=5.
    grouped=defaultdict_list(report["pending"])
    for kind,rows in grouped.items():
        if kind in ("version_unknown","distribution_unknown"):continue
        page(rows[0]["label"]+"｜"+str(len(rows))+"条")
        p("按证据扫描顺序列最多5条；完整条目见同目录待核验CSV。根因未确认时不判定断联或永久丢包。","small")
        for i,q in enumerate(rows[:5],1):
            lines=[f"样例{i}｜"+str(q.get("source",""))+" "+str(q.get("contract","")),
                "原因："+q["reason"],
                "事件/起点："+str(q.get("event","未记录"))+"；接收/终点："+str(q.get("received","未记录")),
                "批次："+str(q.get("run","不适用"))+"；会话："+str(q.get("session","未记录")),
                "序号："+str(q.get("sequence","未记录"))+"；expected="+str(q.get("expected",""))+"；actual="+str(q.get("actual","")),
                "数值："+str(q.get("value","不可得"))+"；事件ns="+str(q.get("event_ns",""))+"；接收ns="+str(q.get("receive_ns","")),
                "原字段："+str(q.get("raw","")),"证据："+str(q.get("evidence",""))+"；帧偏移："+str(q.get("offset",""))]
            story.append(KeepTogether([para(lines[0],"h"),*[para(x,"small") for x in lines[1:]],Spacer(1,12)]))
    page("时钟与运行版本")
    p("时钟检测独立于行情，只读已有记录；不执行校时，不用偏移抵扣行情观测差。缺采样期间不可推断正常。")
    for g in report["clock"]["groups"]:
        p(f"{g['host']} / {g['server']}","h")
        p(f"采样{g['count']}；有效{g['valid']}；失败{g['failed']}；超限{g['exceeded']}；平均{fmt(g['mean'])} ms；最大绝对偏移{fmt(g['max_abs'])} ms。")
        p(str(g["first"])+" 至 "+str(g["last"]),"small")
    if not report["clock"]["groups"]:p("无时钟数据，偏移不可得。")
    versions=Counter((r["version"]["tag"],r["version"]["commit"],str(r["version"]["dirty"])) for r in report["runs"])
    table([["运行tag / commit / dirty","批次数"],*[[str(k),str(n)] for k,n in versions.items()]],[440,65])
    p("未知版本的逐批定位见CSV；同一天多个版本不能只用最新tag代表。所有已读取批次的状态不证明当前进程存活。","small")
    p("整批次累计平均值（不同于当日值）","h")
    cumulative=defaultdict_cumulative(report["runs"])
    table([["源","批次样本总数","累计Mean ms"],*[[k,str(v[0]),fmt(v[1]/v[0] if v[0] else None)] for k,v in cumulative.items()]],[225,130,150])
    p("累计值取覆盖本日的各批次全量时间有效记录并按样本加权；可能跨日期，跨批次重叠不能当作唯一行情条数。","small")
    p("ZMQ：发送成功不等于消费成功。当前分析没有消费ACK对账，实际分发丢包率不可得；未配置endpoint的批次见CSV。","small")
    def footer(c,d):
        c.setStrokeColor(teal);c.line(45,806,550,806);c.setFont("ReportCN",8)
        c.drawString(45,24,report["date"]+" / "+report["version_label"]);c.drawRightString(550,24,str(d.page))
    SimpleDocTemplate(str(path),pagesize=(595.28,841.89),leftMargin=45,rightMargin=45,topMargin=50,bottomMargin=44).build(story,onFirstPage=footer,onLaterPages=footer)
    from pypdf import PdfReader
    check=PdfReader(path)
    if not check.pages or not all(p.extract_text().strip() for p in check.pages):raise ValueError("PDF verification failed")

def defaultdict_list(rows):
    groups={}
    for r in rows:groups.setdefault(r["kind"],[]).append(r)
    return groups

def defaultdict_cumulative(runs):
    result={}
    for r in runs:
        for k,v in r["cumulative"].items():
            item=result.setdefault(k,[0,0.0]);item[0]+=v["n"];item[1]+=v["n"]*(v["mean"] or 0)
    return result
