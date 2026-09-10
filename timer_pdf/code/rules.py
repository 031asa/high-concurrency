"""Versioned trading sessions: unknown calendar/product is not a normal day."""
import json
import re
from pathlib import Path
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache

TZ = timezone(timedelta(hours=8))
class Rules:
    def __init__(self, path):
        self.data = json.loads(Path(path).read_text())
        self.version = self.data["version"]
        self.start = date.fromisoformat(self.data["calendar_start"])
        self.end = date.fromisoformat(self.data["calendar_end"])
        self.closed = set()
        for lo, hi in self.data["holiday_ranges"]:
            d, end = date.fromisoformat(lo), date.fromisoformat(hi)
            while d <= end:
                self.closed.add(d); d += timedelta(days=1)
        self.no_night = set(self.data["no_night"])
        self.products = self.data["products"]
        self.gap = float(self.data["gap_seconds"])
        self.reference = float(self.data["reference_seconds"])
        self.tolerance = float(self.data["close_second_tolerance"])
        if self.gap <= 0 or self.reference <= 0 or not 0 <= self.tolerance <= 1:
            raise ValueError("invalid thresholds")

    def group(self, contract):
        m = re.match(r"[A-Za-z]+", contract)
        return self.products.get(m[0]) if m else None

    def known(self, d):
        return self.start <= d <= self.end

    def open(self, d):
        return self.known(d) and d.weekday() < 5 and d not in self.closed

    @staticmethod
    def instant(d, text):
        return datetime.combine(d, time.fromisoformat(text), TZ)

    @lru_cache(maxsize=4096)
    def intervals(self, group, d):
        if group not in self.data["sessions"] or not self.known(d):
            return None
        overrides = self.data.get("overrides", {})
        if d.isoformat() in overrides:
            entry = overrides[d.isoformat()]
            if group not in entry:
                return None
            return tuple((datetime.fromisoformat(a), datetime.fromisoformat(b)) for a,b in entry[group])
        spec = self.data["sessions"][group]
        spans = []
        if self.open(d):
            spans.extend((self.instant(d,a),self.instant(d,b)) for a,b in spec["day"])
        if spec["night"]:
            a,b = spec["night"]
            for origin in (d-timedelta(days=1),d):
                if self.open(origin) and origin.isoformat() not in self.no_night:
                    start, end = self.instant(origin,a),self.instant(origin,b)
                    if end <= start: end += timedelta(days=1)
                    if start.date() == d or end.date() == d:
                        spans.append((start,end))
                elif origin < self.start and origin.isoformat() not in self.no_night:
                    return None
        return tuple(spans)

    def slot(self, contract, moment):
        spans = self.intervals(self.group(contract), moment.date())
        if spans is None: return None, False
        for a,b in spans:
            if a <= moment < b + timedelta(seconds=self.tolerance):
                return (a,b), True
        return None, True

    def classify(self, q):
        if not q["valid"] or not q["market"] or q["market"] < 0:
            return "invalid_time", None
        try:
            e = datetime.fromtimestamp(q["market"]/1e9,TZ)
            r = datetime.fromtimestamp(q["received"]/1e9,TZ)
        except (ValueError,OverflowError,OSError):
            return "invalid_time", None
        lag = abs(q["received"]-q["market"])/1e6
        es,ek = self.slot(q["contract"],e); rs,rk = self.slot(q["contract"],r)
        if not ek or not rk: return "unknown_rule", lag
        if e.date() > r.date(): return "future_date", lag
        if not es: return "session_snapshot", lag
        if es != rs and lag > self.reference*1000:
            return ("session_snapshot" if rs is None else "cross_session_review"),lag
        # Across midnight within one night session remains valid.
        if e.date() != r.date() and es != rs: return "cross_date_review",lag
        return "live",lag

    def active_seconds(self, contracts, a, b):
        spans = []
        day = a.date()
        while day <= b.date():
            for group in {self.group(c) for c in contracts}:
                items = self.intervals(group,day)
                if items is None: return None
                spans.extend((max(a,x),min(b,y)) for x,y in items if max(a,x)<min(b,y))
            day += timedelta(days=1)
        merged=[]
        for x,y in sorted(spans):
            if merged and x <= merged[-1][1]: merged[-1]=(merged[-1][0],max(merged[-1][1],y))
            else: merged.append((x,y))
        return sum((y-x).total_seconds() for x,y in merged)
