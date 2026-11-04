"""The assistant talking to you: instant pings, bill reminders, payday briefs, weekly/monthly reports.
Every message is queued in the outbox table and sent once (quiet hours respected)."""
import json
import os
import smtplib
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from email.message import EmailMessage

from . import insights as ins
from .insights import nice
from . import planner
from .ingest_email import inboxes

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (key TEXT PRIMARY KEY, title TEXT, body TEXT, urgent INTEGER DEFAULT 0,
  created TEXT DEFAULT (datetime('now')), sent_at TEXT, channels TEXT);
"""

money = lambda v: ("−" if v < 0 else "") + f"${abs(v):,.2f}"
money0 = lambda v: ("−" if v < 0 else "") + f"${abs(v):,.0f}"


def _queue(con, key, title, body, urgent=False):
    con.execute("INSERT OR IGNORE INTO outbox(key, title, body, urgent) VALUES (?,?,?,?)", (key, title, body, int(urgent)))


# ───────────────────────── what's worth saying ─────────────────────────

def detect(con, cfg, today=None):
    con.executescript(SCHEMA)
    today = today or date.today()
    nc = cfg["notify"]
    big = float(nc.get("large_purchase", 150))
    last_id = int((con.execute("SELECT value FROM meta WHERE key='notified_upto'").fetchone() or {"value": 0})["value"] or 0)
    first_run = last_id == 0
    new = con.execute("SELECT * FROM transactions WHERE id>? ORDER BY id", (last_id,)).fetchall()
    if new:
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('notified_upto', ?)", (str(new[-1]["id"]),))
    recent = (today - timedelta(days=3)).isoformat()
    if not first_run:  # don't spam the whole backfill on day one
        for t in new:
            if t["date"] < recent:
                continue
            amt = -t["amount"]
            if t["category"] == "Salary" and t["amount"] > 0:
                s = planner.safe_to_spend(con, cfg, today)
                bills = ", ".join(f"{b['merchant']} {money0(b['amount'])}" for b in s["upcoming_bills"][:4]) or "nothing big"
                _queue(con, f"pay:{t['id']}", f"💰 Payday: {money(t['amount'])}",
                       f"{money(t['amount'])} from {t['merchant']} just landed.\n"
                       f"Still due this month: {bills}.\n→ Safe to spend for the rest of the month: {money0(s['safe_to_spend'])} (~{money0(s['per_day'])}/day), "
                       f"after keeping {int(cfg['plan']['savings_goal_pct'])}% for savings.", True)
            elif t["kind"] == "expense" and amt >= big:
                _queue(con, f"big:{t['id']}", f"💳 {money(amt)} at {t['merchant']}",
                       f"{money(amt)} at {t['merchant']} ({t['category']}) on {t['date']}. Not you? Check it now.", True)
            elif t["kind"] == "expense" and t["merchant"]:
                hist = [r[0] for r in con.execute("SELECT -amount FROM transactions WHERE merchant=? AND kind='expense' AND id!=? AND date<?",
                                                  (t["merchant"], t["id"], t["date"]))]
                if len(hist) >= 3:
                    import statistics
                    med = statistics.median(hist)
                    if amt > max(3 * med, med + 40):
                        _queue(con, f"odd:{t['id']}", f"👀 Unusual: {money(amt)} at {t['merchant']}",
                               f"You usually spend about {money(med)} there. This one was {money(amt)} ({amt/med:.1f}×).", True)
            elif t["counterparty"] and t["amount"] > 0 and t["kind"] in ("income", "reimbursement"):
                memo = f" — “{t['memo']}”" if t["memo"] else ""
                how = "counted as paid back" if t["kind"] == "reimbursement" else "counted as income"
                _queue(con, f"etr:{t['id']}", f"📥 {t['merchant']} sent {money0(t['amount'])}",
                       f"{t['merchant']} sent you {money(t['amount'])}{memo}. Filed as {t['category']} ({how}). Wrong? Fix it in Review.")

    # bills due tomorrow
    tomorrow = (today + timedelta(days=1)).isoformat()
    due = [r for r in ins.recurring(con) if r["active"] and r["next_expected"] == tomorrow]
    if due:
        _queue(con, f"bills:{tomorrow}", "🗓️ Tomorrow: " + ", ".join(r["merchant"] for r in due),
               "Going out tomorrow:\n" + "\n".join(f"• {r['merchant']} — {money(r['amount'])}" for r in due)
               + f"\nTotal {money(sum(r['amount'] for r in due))}. Make sure the account it comes from has it.")

    # missing paycheque
    pc = planner.pay_cycle(con, today)
    if pc and pc["overdue_days"] >= 3:
        _queue(con, f"paymissing:{pc['next']}", "⚠️ Paycheque hasn't arrived",
               f"Your {pc['payer']} pay (~{money0(pc['typical'])}, every {pc['every_days']} days) was due around "
               f"{(date.fromisoformat(pc['last']) + timedelta(days=pc['every_days'])).isoformat()} and hasn't shown up yet.", True)

    # budgets
    mo = today.strftime("%Y-%m")
    for b in planner.budgets(con, cfg, mo):
        if b["status"] == "over" and not b["fixed"]:
            _queue(con, f"over:{mo}:{b['category']}", f"🚨 {b['category']} over budget",
                   f"{b['category']}: {money0(b['spent'])} of {money0(b['budget'])} this month{' (learned budget)' if b['auto'] else ''}.")
        elif b["status"] == "hot":
            _queue(con, f"hot:{mo}:{b['category']}", f"🔥 {b['category']} running hot",
                   f"{b['category']} is at {money0(b['spent'])} with {int((1 - today.day / 30) * 100)}% of the month left "
                   f"(budget {money0(b['budget'])}). Ease off for a bit and you'll land fine.")

    # subscription price changes / new subscriptions
    for s in ins.recurring(con):
        if not s["active"]:
            continue
        prev = con.execute("SELECT -amount a FROM transactions WHERE merchant=? AND kind='expense' ORDER BY date DESC LIMIT 1 OFFSET 1",
                           (s["merchant"],)).fetchone()
        if prev and abs(prev["a"] - s["amount"]) >= 0.5 and abs(prev["a"] - s["amount"]) / prev["a"] > 0.04:
            _queue(con, f"price:{s['merchant']}:{s['amount']}", f"📈 {s['merchant']} changed price",
                   f"{s['merchant']} went from {money(prev['a'])} to {money(s['amount'])} ({money0((s['amount'] - prev['a']) * 12)}/yr difference).")
        if s["count"] == 3 and s["cadence"] == "monthly":
            _queue(con, f"newsub:{s['merchant']}", f"🔁 New subscription spotted: {s['merchant']}",
                   f"{s['merchant']} has charged {money(s['amount'])} three months running — that's {money0(s['yearly_cost'])}/yr. Still using it?")

    # low balance alerts from the bank → tell him where to pull money from
    for h in con.execute("SELECT * FROM balance_hints WHERE date>=?", ((today - timedelta(days=2)).isoformat(),)):
        acct = con.execute("SELECT name FROM accounts WHERE id=?", (h["account_id"],)).fetchone()
        _queue(con, f"low:{h['account_id']}:{h['date']}", f"🟠 {acct['name'] if acct else h['account_id']} is under ${h['max_balance']:,.0f}",
               f"Your bank flagged {acct['name'] if acct else h['account_id']} as under ${h['max_balance']:,.0f}. "
               "If card payments or e-transfers come out of it this week, move money over from Wealthsimple.", True)

    # life events coming up (7 days out + day of)
    for e in cfg["plan"].get("events", []):
        when = e.get("date") or e.get("start")
        if not when:
            continue
        days = (date.fromisoformat(when) - today).days
        if days in (7, 0) or (0 < days <= 7 and e.get("kind") in ("stop", "trip")):
            from .forecast import forecast
            fc = forecast(con, cfg, today)
            extra = (f" Forecast low point after that: {money0(fc['low']['balance'])} on {nice(fc['low']['date'])}." if not fc["relative"] else "")
            label = "Today" if days == 0 else f"In {days} days"
            _queue(con, f"event:{e['name']}:{days == 0}", f"📅 {label}: {e['name']}",
                   f"{label}: {e['name']}." + (f" Amount: {money(abs(float(e['amount'])))}." if e.get("amount") else "") + extra)

    # forecast: will you dip below your buffer?
    try:
        from .forecast import forecast
        fc = forecast(con, cfg, today)
        if not fc["relative"] and fc["below_buffer"]:
            _queue(con, f"fcbuf:{fc['below_buffer'][:7]}", "📉 Heads up on cash",
                   f"At your current pace you'll drop below your {money0(fc['buffer'])} buffer around {nice(fc['below_buffer'])} "
                   f"(lowest {money0(fc['low']['balance'])} on {nice(fc['low']['date'])}). Ledger is holding back "
                   f"{money0(planner.safe_to_spend(con, cfg, today)['hold_back'])}/mo of your safe-to-spend to cover it.")
    except Exception:
        pass

    # goals reached
    try:
        from .goals import goals_status
        for g in goals_status(con, cfg)["goals"]:
            if g["done"]:
                _queue(con, f"goal:{g['name']}:{g['target']}", f"🎯 Goal funded: {g['name']}", f"{g['name']} is fully covered ({money0(g['target'])}). Nice.")
    except Exception:
        pass

    # intake health
    for src in con.execute("SELECT * FROM sources WHERE fail_streak>=3"):
        _queue(con, f"health:{src['key']}:{(src['last_error_at'] or '')[:10]}", "🩺 Ledger can't read an inbox",
               f"{src['key']}: {src['last_error']}. Until this is fixed, transactions from it are missing. "
               "Usually: the Gmail app password was revoked/changed — make a new one and put it in .env.", True)
    unp = con.execute("SELECT count(*) FROM unparsed WHERE resolved=0").fetchone()[0]
    if unp:
        _queue(con, f"unparsed:{unp}", "🧩 Some bank emails couldn't be read",
               f"{unp} bank email(s) didn't match any reader. Start Ollama (local AI) and Ledger will read them itself, "
               "or check System → Unread emails.")

    # morning brief
    mb = nc.get("morning_brief", "08:30")
    if mb and datetime.now().strftime("%H:%M") >= mb:
        s = planner.safe_to_spend(con, cfg, today)
        y = today - timedelta(days=1)
        ys, yn = planner.spend_between(con, y.isoformat(), y.isoformat(), flexible_only=True)
        due = [r for r in ins.recurring(con) if r["active"] and r["next_expected"] and r["next_expected"] <= (today + timedelta(days=3)).isoformat()]
        line = (f"Today you can spend {money0(s['today_allowance'])}." if s["safe_to_spend"] > 0 else
                f"You're {money0(-s['safe_to_spend'])} past this month's flexible budget — keep today light.")
        _queue(con, f"brief:{today}", f"☀️ {line}",
               f"{line} Left this month: {money0(s['safe_to_spend'])}.\nYesterday: {money0(ys)} on everyday stuff ({yn} purchases)." +
               (f"\nComing up: " + ", ".join(f"{r['merchant']} {money0(r['amount'])} ({r['next_expected'][5:]})" for r in due) if due else ""))

    # reports
    wk = nc.get("weekly_report", "sun 19:00")
    if wk:
        day, hm = wk.split()
        now = datetime.now()
        if now.strftime("%a").lower().startswith(day[:3].lower()) and now.strftime("%H:%M") >= hm:
            _queue(con, f"weekly:{today.isocalendar()[0]}-{today.isocalendar()[1]}", "📊 Your week in money", weekly_report(con, cfg, today))
    if nc.get("monthly_report", True) and today.day == 1 and datetime.now().hour >= 9:
        lm = (today - timedelta(days=1)).strftime("%Y-%m")
        _queue(con, f"monthly:{lm}", f"🗓️ {datetime.strptime(lm, '%Y-%m'):%B} wrap-up", monthly_report(con, cfg, lm))
    con.commit()


# ───────────────────────── reports ─────────────────────────

def weekly_report(con, cfg, today=None):
    today = today or date.today()
    start = today - timedelta(days=6)
    s, n = planner.spend_between(con, start.isoformat(), today.isoformat())
    f, _ = planner.spend_between(con, start.isoformat(), today.isoformat(), flexible_only=True)
    weeks = [planner.spend_between(con, (start - timedelta(days=7 * k)).isoformat(), (today - timedelta(days=7 * k)).isoformat(), flexible_only=True)[0] for k in range(1, 9)]
    avg = sum(weeks) / len(weeks) if weeks else 0
    cats = con.execute(
        f"""SELECT category, -sum(amount) s FROM transactions WHERE kind IN {ins.SPEND_KINDS} AND date BETWEEN ? AND ?
            GROUP BY category ORDER BY s DESC LIMIT 4""", (start.isoformat(), today.isoformat())).fetchall()
    biggest = con.execute("SELECT merchant, -amount a, date FROM transactions WHERE kind='expense' AND date BETWEEN ? AND ? ORDER BY amount LIMIT 3",
                          (start.isoformat(), today.isoformat())).fetchall()
    st = planner.safe_to_spend(con, cfg, today)
    nxt = [r for r in ins.recurring(con) if r["active"] and r["next_expected"] and r["next_expected"] <= (today + timedelta(days=7)).isoformat()]
    k = ins.kpis(con, today.strftime("%Y-%m"))
    lines = [f"Spent {money0(s)} across {n} transactions. Day-to-day spending (excluding rent, Dad and bills) was {money0(f)} — " + (
        f"{money0(avg - f)} less than your usual week 👍" if f < avg * 0.9 else
        f"{money0(f - avg)} more than your usual week" if f > avg * 1.1 else "about a normal week") + f" (avg {money0(avg)}).", ""]
    if cats:
        lines.append("Where it went: " + ", ".join(f"{c['category']} {money0(c['s'])}" for c in cats))
    if biggest:
        lines.append("Biggest: " + ", ".join(f"{b['merchant']} {money0(b['a'])}" for b in biggest))
    lines += ["", f"This month so far: spent {money0(k['expenses'])}, income {money0(k['income'])}" +
              (f", saving {int(k['savings_rate'] * 100)}%" if k["savings_rate"] is not None else "") + "."]
    lines.append(f"Safe to spend for the rest of the month: {money0(st['safe_to_spend'])} (~{money0(st['per_day'])}/day).")
    if st["structural_gap"] < 0:
        lines.append(f"⚠️ Your fixed bills ({money0(st['fixed_monthly'])}/mo) are more than your expected income ({money0(st['income'])}/mo) — the gap comes out of savings.")
    if nxt:
        lines.append("Coming up this week: " + ", ".join(f"{r['merchant']} {money0(r['amount'])} ({r['next_expected'][5:]})" for r in nxt))
    tip = _tip(con, cfg, today)
    if tip:
        lines += ["", "💡 " + tip]
    return "\n".join(lines)


def monthly_report(con, cfg, month):
    k = ins.kpis(con, month)
    cats = ins.by_category(con, month)["categories"]
    sv = planner.savings(con, cfg)
    goal = int(cfg["plan"]["savings_goal_pct"])
    lines = [f"Income {money0(k['income'])} · Spent {money0(k['expenses'])} · Kept {money0(k['net'])}"
             + (f" ({int(k['savings_rate'] * 100)}% — goal {goal}% {'✅' if k['savings_rate'] >= goal / 100 else '❌'})" if k["savings_rate"] is not None else ""), ""]
    ups = sorted([c for c in cats if c["avg3"] and c["spent"] - c["avg3"] > 30], key=lambda c: c["avg3"] - c["spent"])[:3]
    downs = sorted([c for c in cats if c["avg3"] and c["avg3"] - c["spent"] > 30], key=lambda c: c["spent"] - c["avg3"])[:3]
    lines.append("Top categories: " + ", ".join(f"{c['category']} {money0(c['spent'])}" for c in cats[:5]))
    if ups:
        lines.append("Up vs usual: " + ", ".join(f"{c['category']} +{money0(c['spent'] - c['avg3'])}" for c in ups))
    if downs:
        lines.append("Down vs usual: " + ", ".join(f"{c['category']} −{money0(c['avg3'] - c['spent'])}" for c in downs))
    subs = [r for r in ins.recurring(con) if r["active"]]
    if subs:
        lines.append(f"Recurring: {len(subs)} bills/subscriptions = {money0(sum(r['yearly_cost'] for r in subs) / 12)}/mo")
    if sv["of"]:
        lines.append(f"Last {sv['of']} months: hit your {goal}% goal {sv['hit']}×, kept {money0(sv['saved_6m'])}, invested {money0(sv['invested_6m'])}.")
    tip = _tip(con, cfg, date.today())
    if tip:
        lines += ["", "💡 " + tip]
    return "\n".join(lines)


def _tip(con, cfg, today):
    """One concrete, money-saving nudge based on your own data."""
    start = (today - timedelta(days=90)).isoformat()
    deliv, n = planner.spend_between(con, start, today.isoformat(), category="Food Delivery")
    if deliv > 150:
        return f"Food delivery cost you {money0(deliv)} over the last 90 days ({n} orders). Cooking half of those would save about {money0(deliv * 2)} a year."
    subs = [r for r in ins.recurring(con) if r["active"] and r["category"] in ("Streaming & Music", "Software & AI")]
    if len(subs) >= 3:
        tot = sum(r["yearly_cost"] for r in subs)
        return f"You have {len(subs)} subscriptions costing {money0(tot)}/yr ({', '.join(r['merchant'] for r in subs[:4])}). Cancel one you didn't open this month."
    coffee, n = planner.spend_between(con, start, today.isoformat(), category="Coffee")
    if n >= 20:
        return f"{n} coffee runs in 90 days = {money0(coffee)}. That's ~{money0(coffee * 4)}/yr."
    return None


# ───────────────────────── delivery ─────────────────────────

def _quiet(cfg):
    q = cfg["notify"].get("quiet_hours")
    if not q:
        return False
    a, b = q.split("-")
    now = datetime.now().strftime("%H:%M")
    return now >= a or now < b if a > b else a <= now < b


def send_email(cfg, subject, body):
    ibs = inboxes(cfg)
    if not ibs or not cfg["notify"].get("email", True):
        return False
    ib = ibs[0]
    pw = os.environ.get(ib["password_env"], "").replace(" ", "")
    if not pw:
        return False
    m = EmailMessage()
    m["From"] = f"Ledger <{ib['address']}>"
    m["To"] = cfg["notify"].get("email_to") or ib["address"]
    m["Subject"] = subject
    m.set_content(body + "\n\n— Ledger (open your dashboard for details)")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(ib["address"], pw)
        s.send_message(m)
    return True


def telegram_ok(cfg):
    return cfg["notify"].get("telegram", True) and os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")


def send_telegram(cfg, text, chat_id=None):
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat_id:
        return False
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": "true"}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=20) as r:
        return json.load(r).get("ok", False)


def flush(con, cfg, log=print):
    con.executescript(SCHEMA)
    quiet = _quiet(cfg)
    rows = con.execute("SELECT * FROM outbox WHERE sent_at IS NULL ORDER BY created").fetchall()
    for r in rows:
        if quiet and not (r["urgent"] and r["key"].startswith(("big:", "odd:"))):
            continue
        ch = []
        long = r["key"].startswith(("weekly:", "monthly:"))
        try:
            if telegram_ok(cfg) and send_telegram(cfg, f"{r['title']}\n\n{r['body']}"):
                ch.append("telegram")
            if r["key"].startswith("brief:") and not ch:
                con.execute("UPDATE outbox SET sent_at=datetime('now'), channels='skipped (no telegram)' WHERE key=?", (r["key"],))
                continue
            if (long or not ch) and send_email(cfg, r["title"], r["body"]):
                ch.append("email")
        except Exception as e:
            log(f"  notify: {e}")
            continue
        if ch:
            con.execute("UPDATE outbox SET sent_at=datetime('now'), channels=? WHERE key=?", (",".join(ch), r["key"]))
            log(f"  notify → {','.join(ch)}: {r['title']}")
    con.commit()
