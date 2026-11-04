"""Cash-flow forecast: day by day from today to your horizon (default: first full-time paycheque).
Built from your real patterns (pay cycle, fixed bills, everyday spending) + the life events in config."""
import statistics
from collections import defaultdict
from datetime import date, timedelta

from .balances import cash_position
from .insights import SPEND_KINDS, _months_back
from .planner import FIXED, pay_cycle

FORECAST_FIXED = FIXED | {"Parking & Tolls", "Shared Costs"}


def _d(s):
    return date.fromisoformat(s) if isinstance(s, str) else s


def _cats(ev):
    c = ev.get("category")
    return set(c) if isinstance(c, list) else {c} if c else set()


def _typical_fixed(con, today):
    """Per fixed category: typical monthly net amount + the day of month it usually goes out."""
    prev = _months_back(4, today.replace(day=1))[:-1]
    fx = ",".join("?" * len(FORECAST_FIXED))
    out = {}
    rows = con.execute(f"""SELECT category, substr(date,1,7) mo, -sum(amount) s FROM transactions WHERE kind IN {SPEND_KINDS}
                           AND category IN ({fx}) AND substr(date,1,7) IN (?,?,?) GROUP BY category, mo""", (*FORECAST_FIXED, *prev)).fetchall()
    by = defaultdict(dict)
    for r in rows:
        by[r["category"]][r["mo"]] = r["s"]
    for cat, mos in by.items():
        vals = [v for v in mos.values() if v > 1]
        if not vals:
            continue
        days = [int(r[0][8:10]) for r in con.execute(
            "SELECT date FROM transactions WHERE category=? AND amount<0 AND date>=? ORDER BY date DESC LIMIT 6", (cat, prev[0] + "-01"))]
        out[cat] = dict(amount=round(statistics.median(vals), 2), day=int(statistics.median(days)) if days else 1)
    return out


def _flex_daily(con, today):
    fx = ",".join("?" * len(FORECAST_FIXED))
    start = (today - timedelta(days=90)).isoformat()
    s = con.execute(f"""SELECT -sum(amount) FROM transactions WHERE kind IN {SPEND_KINDS} AND date>? AND date<=?
                        AND category NOT IN ({fx}) AND COALESCE(tags,'') NOT LIKE '%trip:%'""",
                    (start, today.isoformat(), *FORECAST_FIXED)).fetchone()[0] or 0
    first = con.execute("SELECT min(date) FROM transactions").fetchone()[0]
    span = min(90, max(14, (today - _d(first)).days)) if first else 90
    return max(0.0, s / span)


def _other_income_monthly(con, today):
    prev = _months_back(4, today.replace(day=1))[:-1]
    r = con.execute("SELECT COALESCE(sum(amount),0) FROM transactions WHERE kind='income' AND category NOT IN ('Salary') AND substr(date,1,7) IN (?,?,?)",
                    prev).fetchone()[0]
    return r / 3


def forecast(con, cfg, today=None, horizon=None, extra=None, scenario=None):
    """extra: optional list of hypothetical one-time events [(date, amount, label)] — used by 'can I afford'."""
    today = today or date.today()
    plan = cfg["plan"]
    horizon = _d(horizon or plan.get("horizon") or (today + timedelta(days=365)).isoformat())
    if horizon <= today:
        horizon = today + timedelta(days=180)
    events = plan.get("events", [])
    pos = cash_position(con)
    start = pos["cash"] if pos["complete"] else 0.0
    relative = not pos["complete"]

    pc = pay_cycle(con, today)
    salary_stop = min([_d(e["date"]) for e in events if e.get("kind") == "stop" and "Salary" in _cats(e)] or [date.max])
    fixed = _typical_fixed(con, today)
    flex = _flex_daily(con, today)
    other_inc = _other_income_monthly(con, today)
    buffer = float(plan.get("cash_buffer", 500))

    def stopped(cat, day):
        for e in events:
            if cat not in _cats(e):
                continue
            if e.get("kind") == "stop" and day >= _d(e["date"]):
                return True
            if e.get("kind") == "pause" and _d(e["start"]) <= day <= _d(e["end"]):
                return True
        return False

    series, monthly = [], defaultdict(lambda: defaultdict(float))
    marks = []
    bal = start
    paydays = set()
    if pc:
        nxt = _d(pc["next"])
        while nxt <= horizon:
            if nxt < salary_stop or nxt == salary_stop:
                paydays.add(nxt)
            nxt += timedelta(days=pc["every_days"])
    d = today + timedelta(days=1)
    while d <= horizon:
        mo = d.strftime("%Y-%m")
        flow = []
        if d in paydays:
            flow.append(("income", pc["typical"], "Paycheque"))
        if d.day == 15 and other_inc > 5:
            flow.append(("income", other_inc, "Other income (avg)"))
        for e in events:
            k = e.get("kind")
            if k == "income" and e.get("monthly") and d >= _d(e["date"]) and d.day == 1:
                flow.append(("income", float(e["monthly"]), e["name"]))
            if k == "one_time" and e.get("amount") and _d(e["date"]) == d:
                amt = float(e["amount"])
                flow.append(("income" if amt > 0 else "event", abs(amt), e["name"]))
                marks.append(dict(date=d.isoformat(), label=e["name"], amount=amt))
            if k == "trip" and e.get("budget") and _d(e["start"]) <= d <= _d(e["end"]):
                days = (_d(e["end"]) - _d(e["start"])).days + 1
                flow.append(("event", float(e["budget"]) / days, e["name"]))
        for inc in (scenario or {}).get("incomes", []):
            if d.day == 1 and _d(inc["start"]) <= d <= _d(inc.get("end") or horizon.isoformat()) and inc.get("monthly"):
                flow.append(("income", float(inc["monthly"]), inc.get("label", "What-if income")))
        for x_date, x_amt, x_label in (extra or []) + [(l["date"], float(l["amount"]), l.get("label", "What-if")) for l in (scenario or {}).get("lumps", [])]:
            if _d(x_date) == d:
                flow.append(("event" if x_amt < 0 else "income", abs(x_amt), x_label))
        for cat, f in fixed.items():
            if min(f["day"], 28) == d.day and not stopped(cat, d):
                flow.append(("fixed", f["amount"], cat))
        flow.append(("flex", flex, "Everyday spending"))
        for kind, amt, label in flow:
            bal += amt if kind == "income" else -amt
            monthly[mo][kind] += amt
        monthly[mo]["end"] = bal
        series.append((d.isoformat(), round(bal, 2)))
        d += timedelta(days=1)

    for e in events:  # timeline markers
        dt = e.get("date") or e.get("start")
        if dt and today.isoformat() <= dt <= horizon.isoformat() and not any(m["label"] == e["name"] for m in marks):
            marks.append(dict(date=dt, label=e["name"], amount=e.get("amount") or e.get("budget") or 0))
    low = min(series, key=lambda x: x[1]) if series else (today.isoformat(), start)
    below_buffer = next((s for s in series if s[1] < (buffer if not relative else start - 10**9)), None) if not relative else None
    below_zero = next((s for s in series if s[1] < 0), None) if not relative else None
    end = series[-1][1] if series else start
    return dict(
        start=round(start, 2), relative=relative, horizon=horizon.isoformat(), buffer=buffer,
        series=series[::1], low=dict(date=low[0], balance=low[1]), end=round(end, 2),
        below_buffer=below_buffer[0] if below_buffer else None, below_zero=below_zero[0] if below_zero else None,
        months=[dict(month=m, income=round(v["income"], 2), fixed=round(v["fixed"], 2), flexible=round(v["flex"], 2),
                     events=round(v["event"], 2), end=round(v["end"], 2)) for m, v in sorted(monthly.items())],
        markers=sorted(marks, key=lambda m: m["date"]),
        assumptions=dict(pay=pc and dict(amount=pc["typical"], every_days=pc["every_days"], until=None if salary_stop == date.max else salary_stop.isoformat()),
                         fixed={k: v["amount"] for k, v in fixed.items()}, everyday_per_day=round(flex, 2),
                         other_income_monthly=round(other_inc, 2), missing_balances=pos["missing"]),
    )


def affordability(con, cfg, amount, when=None, today=None):
    """Would spending `amount` (on `when`) push you below your buffer before the horizon?"""
    today = today or date.today()
    base = forecast(con, cfg, today)
    after = forecast(con, cfg, today, extra=[((when or (today + timedelta(days=1)).isoformat()), -abs(amount), "This purchase")])
    if base["relative"]:
        verdict = "unknown"
    else:
        floor = base["buffer"]
        if base["low"]["balance"] < floor:
            verdict = "already_short"
        else:
            verdict = "yes" if after["low"]["balance"] >= floor + 200 else "tight" if after["low"]["balance"] >= floor else \
            "only_by_dipping" if after["low"]["balance"] >= 0 else "no"
    return dict(verdict=verdict, low_before=base["low"], low_after=after["low"], relative=base["relative"], buffer=base["buffer"])
