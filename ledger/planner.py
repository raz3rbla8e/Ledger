"""The assistant's brain: pay cycle, safe-to-spend, budgets, savings — computed from your transactions alone."""
import statistics
from datetime import date, timedelta

from .insights import SPEND_KINDS, _months_back, recurring

FIXED = {"Rent", "Utilities", "Internet & Phone", "Gym", "Software & AI", "Streaming & Music", "Family Support",
         "Car Insurance", "Tuition & School", "Interest Charges"}


def _net_by_month(con, cats, months):
    ph = ",".join("?" * len(cats))
    by = {}
    for r in con.execute(f"""SELECT category, substr(date,1,7) mo, -sum(amount) s FROM transactions WHERE kind IN {SPEND_KINDS}
                             AND category IN ({ph}) AND substr(date,1,7) IN ({",".join("?" * len(months))}) GROUP BY category, mo""",
                         (*cats, *months)):
        by.setdefault(r["category"], {})[r["mo"]] = r["s"]
    return by


def fixed_costs(con, cfg, today=None, cats=None):
    """What each fixed category (rent, Dad, phone, subscriptions…) normally costs per month, NET of what others pay
    you back for it (e.g. a roommate's rent share).
    - If config.toml says it ([plan.fixed] Rent = 1050), that's the answer: what you know beats what Ledger infers.
    - Otherwise: the median of the last 4 complete months, ignoring months with nothing in that category, so a
      one-off month (first & last month's rent, a big catch-up transfer) can't drag the normal amount around."""
    today = today or date.today()
    cats = cats or FIXED
    told = {k: float(v) for k, v in (cfg["plan"].get("fixed") or {}).items()}
    months = _months_back(5, today.replace(day=1))[:-1]
    by = _net_by_month(con, sorted(set(cats) | set(told)), months)
    out = {}
    for cat in set(by) | set(told):
        if cat in told:
            out[cat] = dict(amount=round(told[cat], 2), how="you set it")
            continue
        vals = [by[cat][m] for m in months if by[cat].get(m, 0) > 1]
        if not vals:
            continue
        out[cat] = dict(amount=round(statistics.median(vals), 2),
                        how=f"usual month (median of {len(vals)})")
    return out


def other_income_monthly(con, today=None):
    """Non-salary income you can count on monthly: the median month of the last 3, after setting aside one-off
    lumps (any single deposit of $1,000+ — OSAP, a tax return, a big refund). Those show up as what they are,
    not as a monthly income you don't actually have."""
    today = today or date.today()
    prev = _months_back(4, today.replace(day=1))[:-1]
    by = {m: 0.0 for m in prev}
    for r in con.execute("""SELECT substr(date,1,7) mo, sum(amount) s FROM transactions WHERE kind='income' AND category!='Salary'
                            AND amount < 1000 AND substr(date,1,7) IN (?,?,?) GROUP BY mo""", prev):
        by[r["mo"]] = r["s"] or 0
    return round(statistics.median(by.values()), 2)


def pay_cycle(con, today=None):
    today = today or date.today()
    rows = con.execute(
        "SELECT date, sum(amount) a, max(merchant) m FROM transactions WHERE category='Salary' AND amount>0 AND date>=? GROUP BY date ORDER BY date",
        ((today - timedelta(days=200)).isoformat(),)).fetchall()
    if not rows:
        return None
    dates = [date.fromisoformat(r["date"]) for r in rows]
    gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days >= 5]
    gap = round(statistics.median(gaps)) if gaps else 14
    gap = 14 if 12 <= gap <= 16 else 7 if 6 <= gap <= 8 else gap if gap < 40 else 30
    last = dates[-1]
    nxt = last + timedelta(days=gap)
    while nxt <= today - timedelta(days=4):  # a pay was skipped / missing
        nxt += timedelta(days=gap)
    typical = statistics.median([r["a"] for r in rows[-6:]])
    return dict(last=last.isoformat(), next=nxt.isoformat(), every_days=gap, typical=round(typical, 2),
                last_amount=round(rows[-1]["a"], 2), payer=rows[-1]["m"],
                overdue_days=max(0, (today - (last + timedelta(days=gap))).days))


def safe_to_spend(con, cfg, today=None):
    """Monthly plan, smoothed so rent week doesn't look like a crisis:
       expected income − savings goal − fixed bills (rent, Dad, subscriptions…) = flexible money for the month.
       Safe to spend = flexible money − flexible spending so far this month."""
    import calendar
    today = today or date.today()
    pc = pay_cycle(con, today)
    goal = cfg["plan"].get("savings_goal_pct", 20) / 100
    m0 = today.replace(day=1)
    nd = calendar.monthrange(today.year, today.month)[1]
    end = m0 + timedelta(days=nd)
    prev = _months_back(4, m0)[:-1]
    other_m = other_income_monthly(con, today)
    paydays = []
    if pc:
        # this month's pay = what has actually landed + each payday still to come this month (4 or 5 of them for
        # weekly pay), stopping when a plan event ends your pay (e.g. the co-op's last day)
        stop = min([date.fromisoformat(e["date"]) for e in cfg["plan"].get("events", [])
                    if e.get("kind") == "stop" and "Salary" in (e["category"] if isinstance(e.get("category"), list) else [e.get("category")])]
                   or [date.max])
        got = con.execute("SELECT COALESCE(sum(amount),0), count(*) FROM transactions WHERE category='Salary' AND amount>0 AND date>=? AND date<=?",
                          (m0.isoformat(), today.isoformat())).fetchone()
        nd_ = date.fromisoformat(pc["next"])
        while nd_ < end:
            if today < nd_ <= stop:
                paydays.append(nd_.isoformat())
            nd_ += timedelta(days=pc["every_days"])
        pay_m = round(got[0] + pc["typical"] * len(paydays), 2)
        pay_detail = dict(received=round(got[0], 2), received_count=got[1], coming=paydays, typical=pc["typical"])
    else:
        pay_m = (con.execute(f"SELECT sum(amount) FROM transactions WHERE category='Salary' AND substr(date,1,7) IN (?,?,?)", prev).fetchone()[0] or 0) / 3
        pay_detail = None
    income = round(pay_m + other_m, 2)
    # fixed = what your fixed categories (rent, Dad, phone, subscriptions…) normally cost per month, net of paybacks
    fx = ",".join("?" * len(FIXED))
    items = [dict(merchant=cat, amount=v["amount"], how=v["how"]) for cat, v in fixed_costs(con, cfg, today).items() if v["amount"] > 1]
    # life events: a bill paused/stopped this month (e.g. no Dad transfer in December) isn't a fixed cost this month
    def _off(cat):
        for e in cfg["plan"].get("events", []):
            cats = set(e["category"]) if isinstance(e.get("category"), list) else {e.get("category")}
            if cat not in cats:
                continue
            if e.get("kind") == "stop" and today.isoformat() >= e["date"]:
                return e["name"]
            if e.get("kind") == "pause" and e["start"] <= today.isoformat() <= e["end"]:
                return e["name"]
        return None
    for i in items:
        i["off"] = _off(i["merchant"])
    items = [i for i in items if not i["off"]] + [dict(i, amount=0) for i in items if i["off"]]
    fixed = round(sum(i["amount"] for i in items), 2)
    var_spent = -(con.execute(
        f"SELECT sum(amount) FROM transactions WHERE kind IN {SPEND_KINDS} AND date>=? AND category NOT IN ({fx})",
        (m0.isoformat(), *FIXED)).fetchone()[0] or 0)
    rec = [r for r in recurring(con) if r["active"]]
    upcoming = sorted([dict(merchant=r["merchant"], amount=r["amount"], due=r["next_expected"]) for r in rec
                       if r["next_expected"] and today.isoformat() < r["next_expected"] < end.isoformat()], key=lambda x: x["due"])
    # savings come out only when they fit: after bills, keep at least your usual everyday spending; otherwise the
    # goal is reported as not fitting instead of quietly pushing "safe to spend" below zero
    room = round(income - fixed, 2)
    try:
        from .forecast import _flex_daily
        usual = round(_flex_daily(con, today) * 30.44, 2)
    except Exception:
        usual = 0.0
    goal_amt = round(income * goal, 2)
    save = round(max(0.0, min(goal_amt, room - usual)), 2)
    flex = round(income - save - fixed, 2)
    left = round(flex - var_spent, 2)
    days_left = max(1, (end - today).days)
    # look ahead: if the forecast dips under your buffer (e.g. after the co-op ends), hold some back now
    hold, why = 0.0, None
    try:
        from .forecast import forecast
        fc = forecast(con, cfg, today)
        gap = 0.0
        if not fc["relative"] and fc["low"]["balance"] < fc["buffer"]:
            gap = round(fc["buffer"] - fc["low"]["balance"], 2)
            months_to_low = max(1.0, (date.fromisoformat(fc["low"]["date"]) - today).days / 30.44)
            # hold back a fair share now, but never more than half your flexible money — the rest of the gap
            # has to be closed by income (OSAP, TA, job) or bigger changes, which the Plan page spells out
            hold = round(min(gap / months_to_low, 0.5 * max(0.0, income - save - fixed - usual)), 2)
            why = f"cash is projected to run short by ${gap:,.0f} by {date.fromisoformat(fc['low']['date']):%b %Y}"
    except Exception:
        fc, gap = None, 0.0
    left = round(left - hold, 2)
    return dict(hold_back=hold, hold_reason=why, plan_gap=gap, period_start=m0.isoformat(), period_end=(end - timedelta(days=1)).isoformat(), income=income, pay_monthly=round(pay_m, 2),
                other_income_monthly=round(other_m, 2), pay_detail=pay_detail, savings_set_aside=save, savings_goal=goal_amt,
                room_after_bills=room, usual_everyday=usual, fixed_monthly=fixed,
                fixed_items=sorted(items, key=lambda x: -x["amount"]),
                flexible_budget=flex, spent=round(var_spent, 2), upcoming_bills=upcoming, upcoming_total=round(sum(u["amount"] for u in upcoming), 2),
                safe_to_spend=left, per_day=round(left / days_left, 2), today_allowance=round(max(0, left) / days_left, 2), days_left=days_left, pay=pc, has_pay=bool(pc),
                structural_gap=round(min(0, income - fixed), 2))


def budgets(con, cfg, month=None):
    """Your budgets, or learned ones: median of the last 3 full months, rounded up to $5."""
    today = date.today()
    month = month or today.strftime("%Y-%m")
    y, m = map(int, month.split("-"))
    prev = _months_back(4, date(y, m, 1))[:-1]
    hist = {}
    for r in con.execute(
        f"""SELECT category, substr(date,1,7) mo, -sum(amount) s FROM transactions
            WHERE substr(date,1,7) IN (?,?,?) AND kind IN {SPEND_KINDS} GROUP BY category, mo""", prev):
        hist.setdefault(r["category"], {})[r["mo"]] = r["s"]
    spent = {r["category"]: r["s"] for r in con.execute(
        f"SELECT category, -sum(amount) s FROM transactions WHERE substr(date,1,7)=? AND kind IN {SPEND_KINDS} GROUP BY category", (month,))}
    user = {r["name"]: r["budget"] for r in con.execute("SELECT name, budget FROM categories WHERE budget IS NOT NULL")}
    import calendar
    nd = calendar.monthrange(y, m)[1]
    frac = (today.day / nd) if (today.year, today.month) == (y, m) else 1.0
    out = []
    for cat in set(hist) | set(spent) | set(user):
        if cat in ("Uncategorized",):
            continue
        b = user.get(cat)
        auto = False
        if b is None and cfg["plan"].get("auto_budgets", True) and cat in hist:
            vals = [hist[cat].get(p, 0) for p in prev]
            med = statistics.median(vals)
            if med >= 20:
                b, auto = float(-(-med // 5) * 5), True
        if not b:
            continue
        s = round(spent.get(cat, 0), 2)
        expected = b if cat in FIXED else b * frac
        status = "over" if s > b else "hot" if cat not in FIXED and frac >= 0.25 and s > expected * 1.25 and s - expected > 25 else "ok"
        out.append(dict(category=cat, budget=b, spent=s, left=round(b - s, 2), pct=round(s / b, 3), auto=auto, status=status, fixed=cat in FIXED))
    return sorted(out, key=lambda x: (-x["pct"]))


def savings(con, cfg):
    from .insights import monthly
    ms = monthly(con, 7)
    goal = cfg["plan"].get("savings_goal_pct", 20) / 100
    full = [x for x in ms[:-1] if x["income"] > 0]
    hit = sum(1 for x in full if (x["savings_rate"] or 0) >= goal)
    return dict(goal_pct=goal, months=ms, hit=hit, of=len(full),
                avg_rate=round(statistics.mean([x["savings_rate"] for x in full]), 3) if full else None,
                saved_6m=round(sum(x["net"] for x in full), 2), invested_6m=round(sum(x["invested"] for x in full), 2))


def spend_between(con, start, end, category=None, merchant=None, flexible_only=False):
    sql = f"SELECT -sum(t.amount) s, count(*) n FROM transactions t LEFT JOIN categories c ON c.name=t.category WHERE t.kind IN {SPEND_KINDS} AND t.date BETWEEN ? AND ?"
    args = [start, end]
    if flexible_only:
        sql += f" AND t.category NOT IN ({','.join('?' * len(FIXED))})"
        args += list(FIXED)
    if category:
        sql += " AND (t.category=? OR c.grp=?)"
        args += [category, category]
    if merchant:
        sql += " AND (t.merchant LIKE ? OR t.counterparty LIKE ?)"
        args += [f"%{merchant}%", f"%{merchant}%"]
    r = con.execute(sql, args).fetchone()
    return round(r["s"] or 0, 2), r["n"]


# ───────── paycheque to paycheque ─────────
# You live pay to pay, so the dashboard is built around the pay period (last payday → next payday), not the
# month and not account balances. Each paycheque carries its share of the monthly bills; the rest is yours until
# the next one lands.

BILLS = FIXED | {"Parking & Tolls", "Line of Credit"}
DAYS_PER_MONTH = 30.44


def _events(cfg):
    return cfg["plan"].get("events", [])


def _ev_cats(e):
    c = e.get("category")
    return set(c) if isinstance(c, list) else {c} if c else set()


def _bill_off(cfg, cat, d):
    """The plan event that switches this bill off on day d (Dad paused in December, rent stops when you move)."""
    for e in _events(cfg):
        if cat not in _ev_cats(e):
            continue
        if e.get("kind") == "stop" and d >= e["date"]:
            return e["name"]
        if e.get("kind") == "pause" and e["start"] <= d <= e["end"]:
            return e["name"]
    return None


def _pay_stop(cfg):
    return min([date.fromisoformat(e["date"]) for e in _events(cfg) if e.get("kind") == "stop" and "Salary" in _ev_cats(e)]
               or [date.max])


def bill_list(con, cfg, today=None):
    """Every monthly bill with its amount (what you told Ledger, or the usual month), the day it normally goes out,
    and whether this month's has gone out yet."""
    today = today or date.today()
    m0 = today.replace(day=1)
    fx = fixed_costs(con, cfg, today, BILLS)
    since = (m0 - timedelta(days=100)).isoformat()
    out = []
    for cat, v in fx.items():
        amt = v["amount"]
        if amt <= 1:
            continue
        # the day of month the biggest payment in this category usually goes out
        days = []
        for r in con.execute(f"""SELECT substr(date,1,7) mo, date, amount FROM transactions WHERE category=? AND kind IN {SPEND_KINDS}
                                 AND amount<0 AND date>=? AND date<? ORDER BY amount""", (cat, since, m0.isoformat())):
            if r["mo"] not in {d[0] for d in days}:
                days.append((r["mo"], int(r["date"][8:10])))
        day = int(statistics.median([d for _, d in days])) if days else None
        paid = -(con.execute(f"SELECT COALESCE(sum(amount),0) FROM transactions WHERE category=? AND kind IN {SPEND_KINDS} AND date>=? AND date<=?",
                             (cat, m0.isoformat(), today.isoformat())).fetchone()[0])
        last = con.execute(f"SELECT max(date) FROM transactions WHERE category=? AND kind IN {SPEND_KINDS} AND amount<0 AND date>=?",
                           (cat, m0.isoformat())).fetchone()[0]
        off = _bill_off(cfg, cat, today.isoformat())
        import calendar
        nd = calendar.monthrange(today.year, today.month)[1]
        due = m0.replace(day=min(day, nd)).isoformat() if day else None
        status = ("off" if off else "paid" if paid >= amt * 0.9 else "part" if paid > amt * 0.15 else
                  "untracked" if day is None else "late" if due < today.isoformat() else "due")
        out.append(dict(category=cat, name="Dad" if cat == "Family Support" else cat, amount=amt, how=v["how"], day=day, due=due,
                        paid=round(max(0.0, paid), 2), paid_on=last, status=status, off=off))
    order = {"late": 0, "due": 1, "part": 2, "untracked": 3, "paid": 4, "off": 5}
    return sorted(out, key=lambda b: (order[b["status"]], b["due"] or "9999", -b["amount"]))


def _everyday(con, start, end):
    """Everyday spending (not bills) from start to end, both inclusive, net of what friends paid back."""
    bx = ",".join("?" * len(BILLS))
    return -(con.execute(f"SELECT COALESCE(sum(amount),0) FROM transactions WHERE kind IN {SPEND_KINDS} AND date>=? AND date<=? AND category NOT IN ({bx})",
                         (start, end, *BILLS)).fetchone()[0])


def _income(con, start, end):
    r = con.execute("""SELECT COALESCE(sum(CASE WHEN category='Salary' THEN amount END),0) pay,
                              COALESCE(sum(CASE WHEN category!='Salary' AND amount<1000 THEN amount END),0) other,
                              COALESCE(sum(CASE WHEN category!='Salary' AND amount>=1000 THEN amount END),0) lumps
                       FROM transactions WHERE kind='income' AND amount>0 AND date>=? AND date<=?""", (start, end)).fetchone()
    return round(r["pay"], 2), round(r["other"], 2), round(r["lumps"], 2)


def bills_per_day(con, cfg, d):
    """Monthly bills still switched on at day d, spread per day."""
    fx = fixed_costs(con, cfg, d if isinstance(d, date) else date.fromisoformat(d), BILLS)
    iso = d.isoformat() if isinstance(d, date) else d
    on = {c: v["amount"] for c, v in fx.items() if v["amount"] > 1 and not _bill_off(cfg, c, iso)}
    return sum(on.values()) / DAYS_PER_MONTH, on


def pay_period(con, cfg, today=None):
    """This pay: what came in on payday, the share of monthly bills it has to carry, what you've spent since,
    and what's free until the next one. When no more paycheques are coming (co-op over) it falls back to the
    calendar month, with whatever else comes in."""
    import calendar
    today = today or date.today()
    pc = pay_cycle(con, today)
    stop = _pay_stop(cfg)
    try:
        from .forecast import _flex_daily
        usual_day = _flex_daily(con, today)
    except Exception:
        usual_day = 0.0
    per_day_bills, on = bills_per_day(con, cfg, today)
    if pc and date.fromisoformat(pc["next"]) <= stop + timedelta(days=pc["every_days"]):
        start, nxt = date.fromisoformat(pc["last"]), date.fromisoformat(pc["next"])
        mode = "pay"
        next_is_pay = nxt <= stop
    else:
        start = today.replace(day=1)
        nxt = start + timedelta(days=calendar.monthrange(today.year, today.month)[1])
        mode, next_is_pay = "month", False
    end = nxt - timedelta(days=1)
    length = (nxt - start).days
    pay, other, lumps = _income(con, start.isoformat(), today.isoformat())
    bills = round(per_day_bills * length, 2)
    spent = round(_everyday(con, start.isoformat(), today.isoformat()), 2)
    money_in = round(pay + other, 2)
    free = round(money_in - bills, 2)
    left = round(free - spent, 2)
    days_left = max(1, (nxt - today).days)
    # day by day, for the strip
    daily = {r["date"]: -r["s"] for r in con.execute(
        f"""SELECT date, sum(amount) s FROM transactions WHERE kind IN {SPEND_KINDS} AND date>=? AND date<=?
            AND category NOT IN ({",".join("?" * len(BILLS))}) GROUP BY date""", (start.isoformat(), end.isoformat(), *BILLS))}
    days = []
    for i in range(length):
        d = (start + timedelta(days=i)).isoformat()
        days.append(dict(date=d, spent=round(max(0.0, daily.get(d, 0.0)), 2)))
    # bills that normally go out before the next payday
    due = [b for b in bill_list(con, cfg, today) if b["status"] in ("due", "late", "part") and b["due"] and b["due"] < nxt.isoformat()]
    # next month's bills that fall before the next payday too
    if nxt.month != today.month or nxt.year != today.year:
        for b in bill_list(con, cfg, today):
            if b["day"] and b["status"] != "off":
                d2 = nxt.replace(day=1)
                dd = d2.replace(day=min(b["day"], calendar.monthrange(d2.year, d2.month)[1])).isoformat()
                if dd < nxt.isoformat() and not _bill_off(cfg, b["category"], dd):
                    due.append(dict(b, due=dd, status="due", paid=0))
    return dict(mode=mode, start=start.isoformat(), end=end.isoformat(), next=nxt.isoformat(), next_is_pay=next_is_pay,
                length=length, day=(today - start).days + 1, days_left=days_left,
                pay=pay, other=other, lumps=lumps, money_in=money_in, typical_pay=pc["typical"] if pc else None, every_days=pc["every_days"] if pc else None,
                bills=bills, bills_monthly=round(sum(on.values()), 2), bill_cats=on, spent=spent, free=free, left=left,
                per_day=round(left / days_left, 2), usual=round(usual_day * length, 2), usual_day=round(usual_day, 2),
                days=days, due=sorted(due, key=lambda b: b["due"]), pay_stops=None if stop == date.max else stop.isoformat())


def pay_history(con, cfg, today=None, n=10):
    """Each past paycheque: what it brought in, its share of bills, and the everyday spending until the next one.
    Did the pay cover its stretch?"""
    today = today or date.today()
    rows = [date.fromisoformat(r[0]) for r in con.execute(
        "SELECT DISTINCT date FROM transactions WHERE category='Salary' AND amount>0 AND date<? ORDER BY date DESC LIMIT ?",
        (today.isoformat(), n + 1))][::-1]
    out = []
    for a, b in zip(rows, rows[1:]):
        if (b - a).days > 40:
            continue
        end = (b - timedelta(days=1)).isoformat()
        pay, other, lumps = _income(con, a.isoformat(), end)
        per_day, _ = bills_per_day(con, cfg, a)
        bills = round(per_day * (b - a).days, 2)
        spent = round(_everyday(con, a.isoformat(), end), 2)
        out.append(dict(start=a.isoformat(), end=end, pay=pay, other=other, bills=bills, free=round(pay + other - bills, 2),
                        spent=spent, kept=round(pay + other - bills - spent, 2)))
    return out


def dad_lever(con, cfg, pp):
    """How much Dad's transfer would need to be for your pay to cover bills + your usual everyday spending."""
    dad = pp["bill_cats"].get("Family Support")
    if not dad or pp["mode"] != "pay" or not pp["typical_pay"]:
        return None
    per_pay = lambda monthly: monthly * pp["every_days"] / DAYS_PER_MONTH
    other_per_pay = other_income_monthly(con) * pp["every_days"] / DAYS_PER_MONTH
    free_typical = pp["typical_pay"] + other_per_pay - per_pay(pp["bills_monthly"])
    usual = pp["usual_day"] * pp["every_days"]
    gap_pay = usual - free_typical
    gap_month = gap_pay * DAYS_PER_MONTH / pp["every_days"]
    return dict(dad=dad, per_100=round(per_pay(100), 2), free_typical=round(free_typical, 2), usual_per_pay=round(usual, 2),
                gap_per_pay=round(gap_pay, 2), break_even=round(max(0.0, dad - max(0.0, gap_month)), -1) if gap_month > 0 else dad)
