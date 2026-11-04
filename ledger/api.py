"""Page payloads for the dashboard — one call per page."""
import json
from datetime import date, timedelta

from . import insights as ins
from . import planner
from .balances import cash_position
from .forecast import forecast
from .goals import goals_status



def _live_messages(con):
    """Recent assistant messages, minus ones that are no longer true (emails since read, an older morning brief)."""
    unread = con.execute("SELECT count(*) FROM unparsed WHERE resolved=0").fetchone()[0]
    out, brief_seen = [], False
    for r in con.execute("SELECT key, title, body, created, sent_at, channels FROM outbox ORDER BY created DESC LIMIT 20"):
        k = r["key"] or ""
        if k.startswith("unparsed:") and (not unread or k != f"unparsed:{unread}"):
            continue
        if k.startswith("brief:"):
            if brief_seen:
                continue
            brief_seen = True
        out.append({x: r[x] for x in ("title", "body", "created", "sent_at", "channels")})
        if len(out) == 8:
            break
    return out

def _version(con):
    r = con.execute("SELECT value FROM meta WHERE key='data_version'").fetchone()
    return r["value"] if r else "0"


def upcoming(con, cfg, days=21, today=None):
    today = today or date.today()
    end = (today + timedelta(days=days)).isoformat()
    items = [dict(date=r["next_expected"], label=r["merchant"], amount=-r["amount"], kind="bill", category=r["category"])
             for r in ins.recurring(con) if r["active"] and r["next_expected"] and today.isoformat() <= r["next_expected"] <= end]
    pc = planner.pay_cycle(con, today)
    if pc and today.isoformat() <= pc["next"] <= end:
        items.append(dict(date=pc["next"], label=f"Paycheque ({pc['payer']})", amount=pc["typical"], kind="pay"))
    for e in cfg["plan"].get("events", []):
        d = e.get("date") or e.get("start")
        if d and today.isoformat() <= d <= end:
            items.append(dict(date=d, label=e["name"], amount=float(e.get("amount") or 0) or None, kind="event"))
    return sorted(items, key=lambda x: x["date"])


def today_page(con, cfg, month):
    s = planner.safe_to_spend(con, cfg)
    fc = forecast(con, cfg)
    k = ins.kpis(con, month)
    pos = cash_position(con)
    g = goals_status(con, cfg, fc, s)
    last = con.execute("SELECT at FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    status, tone = _headline(s, fc, k)
    return dict(
        version=_version(con), headline=status, tone=tone, safe=s, kpis=k, cash=pos, today=today_numbers(con, s),
        forecast=dict(low=fc["low"], below_buffer=fc["below_buffer"], end=fc["end"], relative=fc["relative"], horizon=fc["horizon"],
                      series=fc["series"][::3], buffer=fc["buffer"], start=fc["start"]),
        upcoming=upcoming(con, cfg), goals=g,
        activity=[dict(r) for r in con.execute("SELECT at, kind, text FROM activity ORDER BY id DESC LIMIT 12")],
        messages=_live_messages(con),
        recent=[dict(r) for r in con.execute(
            """SELECT t.id, t.date, t.merchant, t.category, t.amount, t.kind, a.name account FROM transactions t
               LEFT JOIN accounts a ON a.id=t.account_id ORDER BY t.date DESC, t.id DESC LIMIT 12""")],
        to_review=con.execute("SELECT count(*) FROM transactions WHERE reviewed=0").fetchone()[0],
        health=_health_summary(con), last_sync=last["at"] if last else None,
        months=[r[0] for r in con.execute("SELECT DISTINCT substr(date,1,7) FROM transactions ORDER BY 1 DESC")],
    )


def today_numbers(con, s):
    """Today's allowance as it stood this morning, what's gone since, and what's left — for the dial and the widget."""
    from datetime import date as _d
    t = _d.today().isoformat()
    spent, n = planner.spend_between(con, t, t, flexible_only=True)
    spent = max(0.0, spent)
    allowance = round((max(0, s["safe_to_spend"]) + spent) / max(1, s["days_left"]), 2)
    return dict(allowance=allowance, spent=round(spent, 2), left=round(allowance - spent, 2), purchases=n)


def _headline(s, fc, k):
    nice = ins.nice
    if s["structural_gap"] < 0:
        return (f"Your fixed bills are ${-s['structural_gap']:,.0f}/mo more than your income — you're running on savings.", "bad")
    if s["safe_to_spend"] < 0:
        return (f"You're ${-s['safe_to_spend']:,.0f} past what's safe this month. Keep the rest of the month light.", "bad")
    if s.get("plan_gap"):
        return (f"This month is fine, but cash runs ${s['plan_gap']:,.0f} short by {nice(fc['low']['date'])} at current habits — "
                "Ledger is holding some back and the Plan tab shows how to close it.", "warn")
    if s["per_day"] < 15:
        return (f"Tight month: about ${s['per_day']:,.0f}/day left for everyday spending.", "warn")
    return (f"You're on track — ${s['safe_to_spend']:,.0f} left to spend this month, about ${s['per_day']:,.0f}/day.", "good")


def _health_summary(con):
    rows = [dict(r) for r in con.execute("SELECT * FROM sources ORDER BY key")]
    bad = [r for r in rows if (r["fail_streak"] or 0) > 0]
    unp = con.execute("SELECT count(*) FROM unparsed WHERE resolved=0").fetchone()[0]
    return dict(ok=not bad and not unp, problems=[f"{r['key']}: {r['last_error']}" for r in bad] + ([f"{unp} unread bank email(s)"] if unp else []))


def spending_page(con, cfg, month):
    y, m = map(int, month.split("-"))
    start = date(y, m, 1)
    heat_from = (date.today() - timedelta(days=364))
    heat = {r["date"]: r["s"] for r in con.execute(
        f"""SELECT date, round(-sum(amount),2) s FROM transactions WHERE kind IN {ins.SPEND_KINDS} AND date>=?
            AND category NOT IN ({','.join('?' * len(planner.FIXED))}) GROUP BY date""", (heat_from.isoformat(), *planner.FIXED))}
    trips = [dict(name=e["name"], start=e["start"], end=e["end"], budget=e.get("budget") or 0,
                  spent=-(con.execute("SELECT COALESCE(sum(amount),0) FROM transactions WHERE tags LIKE ? AND kind IN ('expense','refund')",
                                      (f"%trip:{e['name']}%",)).fetchone()[0]))
             for e in cfg["plan"].get("events", []) if e.get("kind") == "trip"]
    return dict(version=_version(con), kpis=ins.kpis(con, month), categories=ins.by_category(con, month), budgets=planner.budgets(con, cfg, month),
                merchants=ins.top_merchants(con, month, 15), trend=ins.group_trend(con, 12), daily=ins.daily_cumulative(con, month),
                monthly=ins.monthly(con, 12), heat=heat, heat_from=heat_from.isoformat(), flags=ins.anomalies(con, month),
                people=ins.people(con, cfg), trips=trips)


def scenario_from_query(q):
    """?job_start=2027-09&job_monthly=4200&winter_monthly=900&osap=6500 → forecast scenario (nothing saved)."""
    sc = {"incomes": [], "lumps": []}
    try:
        if float(q.get("job_monthly") or 0) > 0:
            start = (q.get("job_start") or "2027-09") + "-01"
            sc["incomes"].append(dict(start=start[:10], monthly=float(q["job_monthly"]), label="Full-time job (what-if)"))
        if float(q.get("winter_monthly") or 0) > 0:
            sc["incomes"].append(dict(start="2027-01-01", end="2027-04-30", monthly=float(q["winter_monthly"]), label="Winter income (what-if)"))
        if float(q.get("osap") or 0) > 0:
            sc["lumps"].append(dict(date="2027-01-08", amount=float(q["osap"]), label="OSAP (what-if)"))
    except ValueError:
        pass
    return sc if sc["incomes"] or sc["lumps"] else None


def plan_page(con, cfg, q=None):
    sc = scenario_from_query(q or {})
    fc = forecast(con, cfg, scenario=sc)
    base = forecast(con, cfg) if sc else None
    s = planner.safe_to_spend(con, cfg)
    return dict(version=_version(con), forecast=dict(fc, series=fc["series"][::2]), scenario=sc,
                base=dict(series=base["series"][::2], low=base["low"], end=base["end"]) if base else None, goals=goals_status(con, cfg, fc, s),
                savings=planner.savings(con, cfg), safe=s, events=cfg["plan"].get("events", []), goal_pct=cfg["plan"]["savings_goal_pct"],
                cash=cash_position(con))


def quality(con, cfg):
    from datetime import datetime
    q = []
    last = con.execute("SELECT max(date) FROM transactions WHERE source IN ('email','push')").fetchone()[0]
    age = (date.today() - date.fromisoformat(last)).days if last else None
    q.append(dict(label="Newest transaction from email", value=ins.nice(last) if last else "none yet", ok=age is not None and age <= 4,
                  hint="" if age is not None and age <= 4 else "check the inbox passwords on this page"))
    n30, unc = con.execute("""SELECT count(*), sum(category IN ('Uncategorized','Misc')) FROM transactions
                              WHERE date>=date('now','-30 day') AND kind!='transfer'""").fetchone()
    pct = (unc or 0) / n30 if n30 else 0
    q.append(dict(label="Sorted automatically (30 days)", value=f"{100 - pct * 100:.0f}%", ok=pct < 0.05, hint=f"{unc or 0} uncategorized"))
    rv = con.execute("SELECT count(*) FROM transactions WHERE reviewed=0").fetchone()[0]
    q.append(dict(label="Waiting for a quick look", value=str(rv), ok=rv == 0, hint="Activity tab"))
    bad = con.execute("SELECT count(*) FROM unparsed WHERE resolved=0").fetchone()[0]
    spoof = con.execute("SELECT count(*) FROM unparsed WHERE reason LIKE 'QUARANTINED%'").fetchone()[0]
    q.append(dict(label="Unreadable / blocked emails", value=str(bad), ok=bad == 0, hint=f"{spoof} blocked as possible fakes" if spoof else ""))
    pos = cash_position(con)
    q.append(dict(label="Balances known", value="all" if pos["complete"] else f"missing {len(pos['missing'])}", ok=pos["complete"],
                  hint="type them once below" if not pos["complete"] else ""))
    b = con.execute("SELECT value FROM meta WHERE key='last_backup'").fetchone()
    q.append(dict(label="Last backup", value=b["value"].replace("ledger-", "").replace(".db", "") if b else "not yet", ok=bool(b), hint="kept 14 days"))
    return q


def system_page(con, cfg):
    from . import secrets_store
    from .ai import provider
    from .ingest_email import inboxes
    import os
    return dict(
        version=_version(con),
        sources=[dict(r) for r in con.execute("SELECT * FROM sources ORDER BY key")],
        inboxes=[dict(address=i["address"], has_password=bool(os.environ.get(i["password_env"]))) for i in inboxes(cfg)],
        unparsed=[dict(r) for r in con.execute("SELECT * FROM unparsed WHERE resolved=0 ORDER BY date DESC LIMIT 30")],
        accounts=cash_position(con)["accounts"],
        rules=[dict(r) for r in con.execute("SELECT * FROM rules ORDER BY origin, id DESC")],
        categories=[dict(r) for r in con.execute("SELECT * FROM categories ORDER BY sort")],
        sync_log=[dict(r, summary=json.loads(r["summary"] or "{}")) for r in con.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 8")],
        ai=provider(cfg), ai_state=__import__("ledger.ai", fromlist=["x"]).ollama_state(cfg), telegram=bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")),
        applepay=bool(os.environ.get("LEDGER_HOOK_TOKEN")), widget=bool(os.environ.get("LEDGER_WIDGET_TOKEN")),
        secrets=secrets_store.BACKEND if secrets_store.keychain_available() else ".env file",
        home=str(cfg["_home"]), inbox=str(cfg["_inbox"]),
        watch=cfg["files"].get("watch", []), quality=quality(con, cfg),
    )
