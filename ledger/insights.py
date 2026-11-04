"""All the numbers behind the dashboard."""
import calendar
import statistics
from collections import defaultdict
from datetime import date, timedelta

SPEND_KINDS = ("expense", "refund", "reimbursement")


def nice(d, year=True):
    """'2027-03-08' → 'Mar 8, 2027' (portable — no %-d, which Windows lacks)."""
    if not d:
        return ""
    d = date.fromisoformat(str(d)[:10])
    return f"{d:%b} {d.day}, {d.year}" if year else f"{d:%b} {d.day}"


def _months_back(n, end=None):
    end = end or date.today()
    y, m = end.year, end.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def monthly(con, months=12):
    ms = _months_back(months)
    rows = con.execute(
        f"""SELECT substr(date,1,7) m,
              sum(CASE WHEN kind='income' THEN amount ELSE 0 END) inc,
              -sum(CASE WHEN kind IN {SPEND_KINDS} THEN amount ELSE 0 END) exp,
              -sum(CASE WHEN kind='transfer' AND category='Investing' AND amount<0 THEN amount ELSE 0 END) inv
            FROM transactions WHERE substr(date,1,7) >= ? GROUP BY m""",
        (ms[0],),
    ).fetchall()
    by = {r["m"]: r for r in rows}
    out = []
    for m in ms:
        r = by.get(m)
        inc, exp = (r["inc"] or 0, r["exp"] or 0) if r else (0, 0)
        out.append(dict(month=m, income=round(inc, 2), expenses=round(exp, 2), net=round(inc - exp, 2),
                        invested=round((r["inv"] or 0) if r else 0, 2),
                        savings_rate=round((inc - exp) / inc, 3) if inc > 0 else None))
    return out


def by_category(con, month):
    rows = con.execute(
        f"""SELECT t.category, c.grp, -sum(t.amount) spent, count(*) n FROM transactions t
            LEFT JOIN categories c ON c.name=t.category
            WHERE substr(t.date,1,7)=? AND t.kind IN {SPEND_KINDS} GROUP BY t.category ORDER BY spent DESC""",
        (month,),
    ).fetchall()
    y, m = map(int, month.split("-"))
    prev = _months_back(4, date(y, m, 1))[:-1]
    avg = {
        r["category"]: (r["s"] or 0) / 3
        for r in con.execute(
            f"""SELECT category, -sum(amount) s FROM transactions WHERE substr(date,1,7) IN ({','.join('?'*3)})
                AND kind IN {SPEND_KINDS} GROUP BY category""",
            prev,
        )
    }
    budgets = {r["name"]: r["budget"] for r in con.execute("SELECT name, budget FROM categories WHERE budget IS NOT NULL")}
    cats = [dict(category=r["category"], group=r["grp"] or "Other", spent=round(r["spent"], 2), n=r["n"],
                 avg3=round(avg.get(r["category"], 0), 2), budget=budgets.get(r["category"])) for r in rows]
    groups = defaultdict(float)
    for c in cats:
        groups[c["group"]] += c["spent"]
    return dict(categories=cats, groups=sorted(([g, round(v, 2)] for g, v in groups.items()), key=lambda x: -x[1]))


def group_trend(con, months=12):
    ms = _months_back(months)
    rows = con.execute(
        f"""SELECT substr(t.date,1,7) m, COALESCE(c.grp,'Other') g, -sum(t.amount) s FROM transactions t
            LEFT JOIN categories c ON c.name=t.category
            WHERE substr(t.date,1,7)>=? AND t.kind IN {SPEND_KINDS} GROUP BY m, g""",
        (ms[0],),
    ).fetchall()
    data = defaultdict(lambda: defaultdict(float))
    totals = defaultdict(float)
    for r in rows:
        data[r["g"]][r["m"]] += r["s"]
        totals[r["g"]] += r["s"]
    return dict(months=ms, series={g: [round(data[g].get(m, 0), 2) for m in ms] for g in data}, totals=dict(totals))


def daily_cumulative(con, month):
    y, m = map(int, month.split("-"))
    pm = date(y, m, 1) - timedelta(days=1)
    pmonth = f"{pm.year:04d}-{pm.month:02d}"

    def cum(mo, ndays):
        rows = {r["d"]: r["s"] for r in con.execute(
            f"SELECT CAST(substr(date,9,2) AS INT) d, -sum(amount) s FROM transactions WHERE substr(date,1,7)=? AND kind IN {SPEND_KINDS} GROUP BY d", (mo,))}
        out, run = [], 0.0
        for d in range(1, ndays + 1):
            run += rows.get(d, 0)
            out.append(round(run, 2))
        return out

    nd = calendar.monthrange(y, m)[1]
    today = date.today()
    cur_days = today.day if (today.year, today.month) == (y, m) else nd
    return dict(days=list(range(1, nd + 1)), current=cum(month, cur_days), previous=cum(pmonth, calendar.monthrange(pm.year, pm.month)[1])[:nd],
                prev_month=pmonth)


def kpis(con, month):
    y, m = map(int, month.split("-"))
    nd = calendar.monthrange(y, m)[1]
    today = date.today()
    in_month = (today.year, today.month) == (y, m)
    r = con.execute(
        f"""SELECT sum(CASE WHEN kind='income' THEN amount ELSE 0 END) inc, -sum(CASE WHEN kind IN {SPEND_KINDS} THEN amount ELSE 0 END) exp,
                   sum(CASE WHEN kind='reimbursement' THEN amount ELSE 0 END) back,
                   count(*) n FROM transactions WHERE substr(date,1,7)=?""", (month,)).fetchone()
    inc, exp = r["inc"] or 0, r["exp"] or 0
    # projection = fixed bills (recurring, counted once at their normal amount)
    #            + variable spend so far + your usual daily variable spend for the days left
    proj = None
    if in_month:
        rec = [x for x in recurring(con) if x["active"] and x["cadence"] == "monthly"]
        rec_names = {x["merchant"] for x in rec}
        fixed_total = sum(x["amount"] for x in rec)
        ph = ",".join("?" * len(rec_names)) or "''"
        var_mtd = con.execute(
            f"SELECT -sum(amount) s FROM transactions WHERE substr(date,1,7)=? AND kind IN {SPEND_KINDS} AND COALESCE(merchant,'') NOT IN ({ph})",
            (month, *rec_names)).fetchone()["s"] or 0
        prev3 = _months_back(4, date(y, m, 1))[:-1]
        var_prev = con.execute(
            f"SELECT -sum(amount) s, count(DISTINCT substr(date,1,7)) n FROM transactions WHERE substr(date,1,7) IN (?,?,?) AND kind IN {SPEND_KINDS} AND COALESCE(merchant,'') NOT IN ({ph})",
            (*prev3, *rec_names)).fetchone()
        daily = (var_prev["s"] or 0) / max(1, (var_prev["n"] or 0) * 30.4) if var_prev["n"] else (var_mtd / max(today.day, 1))
        proj = round(fixed_total + var_mtd + daily * (nd - today.day), 2)
    hist = monthly(con, 4)[:-1]
    avg_exp = statistics.mean([h["expenses"] for h in hist]) if hist else 0
    pend = con.execute("SELECT count(*) c FROM transactions WHERE status='pending'").fetchone()["c"]
    review = con.execute("SELECT count(*) c FROM transactions WHERE reviewed=0").fetchone()["c"]
    back = r["back"] or 0
    return dict(month=month, income=round(inc, 2), expenses=round(exp, 2), net=round(inc - exp, 2),
                spent_gross=round(exp + back, 2), paid_back=round(back, 2),
                savings_rate=round((inc - exp) / inc, 3) if inc > 0 else None, projected_expenses=proj,
                avg_expenses_3m=round(avg_exp, 2), days_in_month=nd, day=today.day if in_month else nd,
                pending=pend, to_review=review, txns=r["n"])


def top_merchants(con, month, limit=12):
    return [dict(r) for r in con.execute(
        f"""SELECT merchant, category, -sum(amount) spent, count(*) n FROM transactions
            WHERE substr(date,1,7)=? AND kind IN ('expense','refund') AND merchant IS NOT NULL GROUP BY merchant HAVING spent>0 ORDER BY spent DESC LIMIT ?""",
        (month, limit))]


def recurring(con):
    """Subscriptions & bills: same merchant, regular gap, similar amount."""
    since = (date.today() - timedelta(days=400)).isoformat()
    rows = con.execute(
        "SELECT merchant, category, date, amount FROM transactions WHERE kind='expense' AND date>=? AND merchant IS NOT NULL ORDER BY merchant, date",
        (since,)).fetchall()
    by = defaultdict(list)
    for r in rows:
        by[(r["merchant"], r["category"])].append((date.fromisoformat(r["date"]), -r["amount"]))
    out = []
    for (mer, cat), items in by.items():
        if len(items) < 2:
            continue
        # collapse same-day duplicates
        gaps = [(b[0] - a[0]).days for a, b in zip(items, items[1:]) if (b[0] - a[0]).days > 0]
        if not gaps:
            continue
        g = statistics.median(gaps)
        amts = [a for _, a in items]
        cv = statistics.pstdev(amts) / statistics.mean(amts) if statistics.mean(amts) else 9
        cadence = "weekly" if 6 <= g <= 8 else "monthly" if 26 <= g <= 35 else "yearly" if 350 <= g <= 380 else None
        if not cadence or cv > 0.25:
            continue
        if cadence == "monthly" and len(items) < 2 or cadence == "weekly" and len(items) < 4:
            continue
        spread_ok = sum(1 for x in gaps if abs(x - g) <= max(4, g * 0.2)) >= max(1, len(gaps) * 0.6)
        if not spread_ok:
            continue
        last = items[-1][0]
        step = {"weekly": 7, "monthly": round(g), "yearly": 365}[cadence]
        nxt = last + timedelta(days=step)
        active = (date.today() - last).days <= step * 1.6
        amt = round(items[-1][1], 2)
        out.append(dict(merchant=mer, category=cat, cadence=cadence, amount=amt, count=len(items), last=last.isoformat(),
                        next_expected=nxt.isoformat() if active else None, active=active,
                        yearly_cost=round(amt * {"weekly": 52, "monthly": 12, "yearly": 1}[cadence], 2)))
    # people you pay in several transfers a month (e.g. Dad: two e-transfers adding up to ~$1,700–2,000)
    seen = {o["merchant"] for o in out}
    months = _months_back(5)[:-1]
    by_cp = defaultdict(dict)
    for r in con.execute(
            f"""SELECT counterparty, max(merchant) merchant, max(category) category, substr(date,1,7) mo, -sum(amount) s, max(date) last
                FROM transactions WHERE kind='expense' AND counterparty IS NOT NULL AND substr(date,1,7) IN ({','.join('?' * 4)})
                GROUP BY counterparty, mo""", months).fetchall():
        by_cp[r["counterparty"]][r["mo"]] = r
    for cp, mos in by_cp.items():
        rows = [mos[m] for m in months if m in mos]
        if len(rows) < 3 or rows[0]["merchant"] in seen:
            continue
        vals = [x["s"] for x in rows]
        mean = statistics.mean(vals)
        if mean < 50 or statistics.pstdev(vals) / mean > 0.3:
            continue
        last = max(x["last"] for x in rows)
        day = int(statistics.median([int(x["last"][8:10]) for x in rows]))
        today = date.today()
        nxt = date(today.year, today.month, min(day, 28))
        if nxt.isoformat() <= last or nxt < today:
            nxt = (nxt.replace(day=1) + timedelta(days=32)).replace(day=min(day, 28))
        out.append(dict(merchant=rows[-1]["merchant"], category=rows[-1]["category"], cadence="monthly", amount=round(statistics.median(vals), 2),
                        count=len(rows), last=last, next_expected=nxt.isoformat(), active=(today - date.fromisoformat(last)).days < 50,
                        yearly_cost=round(statistics.median(vals) * 12, 2), split=True))
    return sorted(out, key=lambda x: (not x["active"], -x["yearly_cost"]))


def anomalies(con, month):
    """Plain-English flags: unusual charges, categories running hot, price changes on subscriptions."""
    flags = []
    since = (date.today() - timedelta(days=45)).isoformat()
    for r in con.execute(
        "SELECT id, date, merchant, category, amount FROM transactions WHERE kind='expense' AND date>=? ORDER BY date DESC", (since,)):
        hist = [x["a"] for x in con.execute(
            "SELECT -amount a FROM transactions WHERE merchant=? AND kind='expense' AND id!=? AND date<?", (r["merchant"], r["id"], r["date"]))]
        amt = -r["amount"]
        if len(hist) >= 3:
            med = statistics.median(hist)
            if amt > max(3 * med, med + 40):
                flags.append(dict(level="warn", txn_id=r["id"], text=f"{r['merchant']}: ${amt:,.2f} on {r['date']} — about {amt/med:.1f}× your usual ${med:,.2f}"))
        elif not hist and amt >= 150:
            flags.append(dict(level="info", txn_id=r["id"], text=f"First time at {r['merchant']}: ${amt:,.2f} on {r['date']}"))
    cats = by_category(con, month)["categories"]
    k = kpis(con, month)
    frac = k["day"] / k["days_in_month"]
    fixed = {"Rent", "Utilities", "Internet & Phone", "Gym", "Software & AI", "Streaming & Music", "Family Support", "Car Insurance", "Tuition & School"}
    for c in cats:
        over_full = c["avg3"] >= 40 and c["spent"] > c["avg3"] * 1.25 and c["spent"] - c["avg3"] > 40
        hot_pace = c["category"] not in fixed and k["day"] >= 10 and c["avg3"] >= 40 and c["spent"] / frac > c["avg3"] * 1.5 and c["spent"] - c["avg3"] * frac > 40
        if over_full:
            flags.append(dict(level="warn", text=f"{c['category']}: ${c['spent']:,.0f} this month — already past your ${c['avg3']:,.0f}/mo average"))
        elif hot_pace:
            flags.append(dict(level="warn", text=f"{c['category']} is running hot: ${c['spent']:,.0f} so far, on pace for ${c['spent']/frac:,.0f} vs ${c['avg3']:,.0f}/mo average"))
        if c.get("budget") and c["spent"] > c["budget"]:
            flags.append(dict(level="alert", text=f"{c['category']} is over budget: ${c['spent']:,.0f} of ${c['budget']:,.0f}"))
    for s in recurring(con):
        prev = con.execute(
            "SELECT -amount a FROM transactions WHERE merchant=? AND kind='expense' ORDER BY date DESC LIMIT 1 OFFSET 1", (s["merchant"],)).fetchone()
        if s["active"] and prev and abs(prev["a"] - s["amount"]) >= 0.5 and abs(prev["a"] - s["amount"]) / prev["a"] > 0.04:
            flags.append(dict(level="info", text=f"{s['merchant']} changed price: ${prev['a']:,.2f} → ${s['amount']:,.2f}"))
    return flags[:25]


def accounts(con, cfg):
    out = []
    for a in con.execute("SELECT * FROM accounts ORDER BY institution, type"):
        r = con.execute(
            """SELECT count(*) n, max(date) last, sum(CASE WHEN substr(date,1,7)=strftime('%Y-%m','now','localtime') THEN amount ELSE 0 END) flow,
                      sum(source='email') e, sum(source IN ('csv','ofx')) s FROM transactions WHERE account_id=?""", (a["id"],)).fetchone()
        out.append(dict(dict(a), n=r["n"], last=r["last"], month_flow=round(r["flow"] or 0, 2), from_email=r["e"] or 0,
                        from_statements=r["s"] or 0, drop_folder=str(cfg["_inbox"] / a["id"])))
    return out


def people(con, cfg=None):
    from .config import my_names
    me = my_names(cfg) if cfg else set()
    return [dict(r) for r in con.execute(
        """SELECT counterparty, sum(CASE WHEN amount>0 THEN amount ELSE 0 END) received, -sum(CASE WHEN amount<0 THEN amount ELSE 0 END) sent,
                  count(*) n, max(date) last FROM transactions WHERE counterparty IS NOT NULL AND kind!='transfer'
           GROUP BY counterparty ORDER BY n DESC LIMIT 40""") if r["counterparty"].upper() not in me][:30]
