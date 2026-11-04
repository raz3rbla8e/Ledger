"""Ask it anything — from Telegram or the dashboard.
Built-in answers work offline; with ANTHROPIC_API_KEY set, free-form questions are answered by Claude using a
summary of your data (totals, categories, recent transactions — no account numbers)."""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

from . import insights as ins
from . import planner
from .insights import nice
from .notify import money, money0, monthly_report, send_telegram, weekly_report

SYN = {
    "food": "Food", "eating out": "Restaurants", "restaurants": "Restaurants", "takeout": "Food Delivery", "delivery": "Food Delivery",
    "uber eats": "Food Delivery", "doordash": "Food Delivery", "groceries": "Groceries", "grocery": "Groceries", "coffee": "Coffee",
    "gas": "Gas", "transport": "Transport", "transit": "Transit", "rent": "Rent", "subscriptions": "Subscriptions", "shopping": "Shopping",
    "clothes": "Clothing", "clothing": "Clothing", "gym": "Gym", "fitness": "Health & Fitness", "dad": "Family Support",
    "alcohol": "Alcohol", "drinks": "Alcohol", "travel": "Travel", "fun": "Fun", "games": "Games", "haircut": "Grooming", "grooming": "Grooming",
}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _range(t, today):
    if "today" in t:
        return today, today, "today"
    if "yesterday" in t:
        y = today - timedelta(days=1)
        return y, y, "yesterday"
    if "last week" in t:
        return today - timedelta(days=13), today - timedelta(days=7), "last week"
    if "week" in t:
        return today - timedelta(days=6), today, "the last 7 days"
    if "last month" in t:
        e = today.replace(day=1) - timedelta(days=1)
        return e.replace(day=1), e, e.strftime("%B")
    if "year" in t:
        return today.replace(month=1, day=1), today, "this year"
    m = re.search(r"since (\w{3})", t)
    if m and m.group(1) in MONTHS:
        mo = MONTHS[m.group(1)]
        y = today.year if mo <= today.month else today.year - 1
        return date(y, mo, 1), today, f"since {date(y, mo, 1):%B %Y}"
    m = re.search(r"\b(?:in )?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b", t)
    if m:
        mo = MONTHS[m.group(1)]
        y = today.year if mo <= today.month else today.year - 1
        s = date(y, mo, 1)
        e = (s + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return s, e, f"{s:%B}"
    return today.replace(day=1), today, "this month"


def builtin(con, cfg, text):
    t = text.lower().strip()
    today = date.today()
    if t in ("/start", "/help", "help"):
        return ("I watch your accounts and ping you when it matters. Ask me things like:\n"
                "• how much on food this month?\n• uber eats since september\n• can I afford $200?\n"
                "• /today — what you can spend today\n• /cash — balances\n• /plan — forecast to Sept 2027\n• /goals\n• /safe — safe to spend this month\n• /week /month — reports\n• /bills — what's due\n• /subs — subscriptions\n• /budget")
    if t.startswith("/safe") or "safe to spend" in t or "afford" in t or "how much can i" in t or t.startswith("/today"):
        s = planner.safe_to_spend(con, cfg)
        m = re.search(r"\$?\s?(\d[\d,]*(?:\.\d+)?)", t)
        head = ""
        if m and "afford" in t:
            from .forecast import affordability
            x = float(m.group(1).replace(",", ""))
            a = affordability(con, cfg, x)
            month_ok = x <= s["safe_to_spend"]
            if a["relative"]:
                head = (f"{'Yes' if month_ok else 'Not this month'} — {money0(x)} {'fits in' if month_ok else 'is more than'} what's left this month "
                        f"({money0(s['safe_to_spend'])}). Set your balances once and I can check it against the whole plan to Sept 2027.\n\n")
            else:
                v = a["verdict"]
                head = {"yes": f"Yes. {money0(x)} is fine — your lowest point before {nice(a['low_after']['date'])} would still be {money0(a['low_after']['balance'])}.",
                        "tight": f"Technically yes, but it's tight: your cash would bottom out at {money0(a['low_after']['balance'])} on {nice(a['low_after']['date'])} (buffer {money0(a['buffer'])}).",
                        "only_by_dipping": f"Only by eating your buffer — you'd bottom out at {money0(a['low_after']['balance'])} on {nice(a['low_after']['date'])}.",
                        "no": f"No. That puts you {money0(-a['low_after']['balance'])} in the hole around {nice(a['low_after']['date'])}.",
                        "already_short": (f"Careful — your plan is already short: cash bottoms out at {money0(a['low_before']['balance'])} around "
                                          f"{nice(a['low_before']['date'])} (before any new purchases). {money0(x)} makes that {money0(a['low_after']['balance'])}. "
                                          + ("It fits this month's flexible money, but it's money you'll want later." if month_ok else "I'd skip it."))}[v]
                if not month_ok and v in ("yes", "tight"):
                    head += f" (It's more than this month's flexible money, {money0(s['safe_to_spend'])}, so it comes out of savings.)"
                head += "\n\n"
        fixed = "\n".join(f"  • {f['merchant']} {money0(f['amount'])}" + (f" (off: {f['off']})" if f.get("off") else "") for f in s["fixed_items"][:9]) or "  • none detected yet"
        gap = (f"\n\n⚠️ Fixed bills are {money0(-s['structural_gap'])}/mo more than expected income — that comes out of savings." if s["structural_gap"] < 0 else "")
        hold = f"\nHolding back {money0(s['hold_back'])}/mo because the {s['hold_reason']}." if s.get("hold_back") else ""
        return (head + f"Today: {money0(s['today_allowance'])}. Rest of the month: {money0(s['safe_to_spend'])} (~{money0(s['per_day'])}/day for {s['days_left']} days)\n\n"
                f"Expected income {money0(s['income'])}/mo − save {money0(s['savings_set_aside'])} − fixed bills {money0(s['fixed_monthly'])} "
                f"= {money0(s['flexible_budget'])} flexible; spent {money0(s['spent'])} so far.{hold}\n\nFixed bills:\n{fixed}{gap}")
    if t.startswith("/cash") or "balance" in t or "how much do i have" in t:
        from .balances import cash_position
        p = cash_position(con)
        lines = [f"• {a['name']}: {money(a['balance']) if a['balance'] is not None else 'unknown'}" + (" (approx)" if a.get("approx") else "") for a in p["accounts"]]
        return (f"Cash: {money0(p['cash'])}" + (" (incomplete — some balances unknown)" if not p["complete"] else "") + "\n" + "\n".join(lines) +
                ("\n\nSet the unknown ones once on the dashboard (System → Accounts) and I'll keep them current." if p["missing"] else ""))
    if t.startswith("/plan") or "runway" in t or "forecast" in t or "last until" in t:
        from .forecast import forecast
        f = forecast(con, cfg)
        if f["relative"]:
            return (f"Cash-flow change through {nice(f['horizon'])}: {money0(f['end'])} (lowest {money0(f['low']['balance'])} on {nice(f['low']['date'])}). "
                    "Set your account balances once and I'll turn this into real numbers + a runway date.")
        msg = f"Now {money0(f['start'])} → {money0(f['end'])} by {nice(f['horizon'])}. Lowest: {money0(f['low']['balance'])} on {nice(f['low']['date'])}."
        if f["below_zero"]:
            msg += f"\n⚠️ At this pace you run out around {nice(f['below_zero'])}."
        elif f["below_buffer"]:
            msg += f"\nDips under your {money0(f['buffer'])} buffer around {nice(f['below_buffer'])}."
        return msg + "\n\nBy month:\n" + "\n".join(f"{m['month']}: in {money0(m['income'])}, out {money0(m['fixed'] + m['flexible'] + m['events'])} → {money0(m['end'])}" for m in f["months"][:10])
    if t.startswith("/goals") or "goal" in t:
        from .goals import goals_status
        g = goals_status(con, cfg)
        if g["needs_balances"]:
            return "Goals are funded from your cash — set your balances once (System → Accounts) and I'll track them.\n" + "\n".join(f"• {x['name']}: {money0(x['target'])}" for x in g["goals"])
        return "\n".join(f"{'✅' if x['done'] else '•'} {x['name']}: {money0(x['funded'])} / {money0(x['target'])}" + (f" — save {money0(x['per_month'])}/mo" if x["per_month"] else "") for x in g["goals"])
    if t.startswith("/week"):
        return weekly_report(con, cfg)
    if t.startswith("/month"):
        return monthly_report(con, cfg, today.strftime("%Y-%m"))
    if t.startswith("/subs") or "subscription" in t:
        subs = [r for r in ins.recurring(con) if r["active"]]
        return "\n".join(f"• {r['merchant']}: {money(r['amount'])} {r['cadence']} → {money0(r['yearly_cost'])}/yr" for r in subs) + \
               f"\nTotal {money0(sum(r['yearly_cost'] for r in subs))}/yr" if subs else "No recurring charges detected yet."
    if t.startswith("/bills") or "due" in t or "bills" in t:
        nxt = [r for r in ins.recurring(con) if r["active"] and r["next_expected"] and r["next_expected"] <= (today + timedelta(days=14)).isoformat()]
        return "Next 14 days:\n" + "\n".join(f"• {r['next_expected'][5:]} {r['merchant']} {money(r['amount'])}" for r in sorted(nxt, key=lambda r: r["next_expected"])) if nxt else "Nothing due in the next 2 weeks."
    if t.startswith("/budget") or "budget" in t:
        bs = planner.budgets(con, cfg)
        return "This month:\n" + "\n".join(f"{'🚨' if b['status']=='over' else '🔥' if b['status']=='hot' else '•'} {b['category']}: {money0(b['spent'])} / {money0(b['budget'])}" for b in bs[:15])
    # spend questions
    if re.search(r"how much|spent|spend|total", t):
        s, e, label = _range(t, today)
        cat = None
        for k in sorted(SYN, key=len, reverse=True):
            if re.search(r"\b" + re.escape(k) + r"\b", t):
                cat = SYN[k]
                break
        if not cat:
            for r in con.execute("SELECT name FROM categories UNION SELECT DISTINCT grp FROM categories"):
                if r[0] and r[0].lower() in t:
                    cat = r[0]
                    break
        mer = None
        if not cat:
            for r in con.execute("SELECT DISTINCT merchant FROM transactions WHERE merchant IS NOT NULL"):
                if r[0] and len(r[0]) >= 4 and r[0].lower() in t:
                    mer = r[0]
                    break
        total, n = planner.spend_between(con, s.isoformat(), e.isoformat(), cat, mer)
        what = cat or mer or "everything"
        return f"{what} {label}: {money(total)} across {n} transactions."
    return None


def _context(con, cfg):
    today = date.today()
    m = today.strftime("%Y-%m")
    cats6 = {}
    for r in con.execute(f"""SELECT substr(date,1,7) mo, category, round(-sum(amount),2) s FROM transactions WHERE kind IN {ins.SPEND_KINDS}
                             AND date>=? GROUP BY mo, category""", ((today - timedelta(days=190)).isoformat(),)):
        cats6.setdefault(r["mo"], {})[r["category"]] = r["s"]
    recent = [dict(r) for r in con.execute(
        "SELECT date, merchant, category, kind, amount, memo FROM transactions ORDER BY date DESC, id DESC LIMIT 80")]
    return dict(today=today.isoformat(), this_month=ins.kpis(con, m), monthly=ins.monthly(con, 12), spend_by_category_by_month=cats6,
                budgets=planner.budgets(con, cfg), safe_to_spend=planner.safe_to_spend(con, cfg), recurring=ins.recurring(con),
                savings=planner.savings(con, cfg), savings_goal_pct=cfg["plan"]["savings_goal_pct"], recent_transactions=recent)


def ask_llm(con, cfg, question):
    from .ai import complete, provider
    mode = cfg["ai"].get("chat", "local")
    which = "anthropic" if mode == "claude" and os.environ.get("ANTHROPIC_API_KEY") else ("ollama" if provider(cfg) == "ollama" or (cfg["ai"].get("provider") == "ollama") else None)
    if not which:
        return None
    who = cfg["me"].get("name") or "the user"
    about = cfg["me"].get("about", "")
    sys = (f"You are {who}'s personal money assistant inside their self-hosted tracker (CAD). Negative = money out. "
           "kind=transfer is between their own accounts. " + (f"Context: {about} " if about else "") +
           "Be brief (phone-sized), concrete, use their real numbers, and end with one practical suggestion when useful. If the data can't answer, say so.")
    return complete(cfg, f"My data:\n{json.dumps(_context(con, cfg), default=str)[:60000]}\n\nQuestion: {question}", json_mode=False, system=sys, which=which)


def answer(con, cfg, text, lock=None):
    lock = lock or threading.RLock()
    with lock:
        t = text.strip()
        out = builtin(con, cfg, t)
        if out:
            return out
        try:
            out = ask_llm(con, cfg, t)
        except Exception:
            out = None
        return out or "I didn't catch that — try /help. (Start Ollama on your PC and I can answer free-form questions locally.)"


def telegram_loop(con, cfg, lock, log=print):
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        return
    mine = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    offset = 0
    log("Telegram bot listening")
    while True:
        try:
            url = f"https://api.telegram.org/bot{tok}/getUpdates?timeout=50&offset={offset}"
            with urllib.request.urlopen(url, timeout=70) as r:
                ups = json.load(r).get("result", [])
            for u in ups:
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat = str((msg.get("chat") or {}).get("id", ""))
                text = msg.get("text") or ""
                if not chat or not text:
                    continue
                if not mine:
                    send_telegram(cfg, f"Hi! Your chat id is {chat}. Put TELEGRAM_CHAT_ID={chat} in your Ledger .env and restart — "
                                       "after that I'll only ever talk to this chat.", chat)
                    continue
                if chat != mine:
                    continue  # strangers get nothing
                send_telegram(cfg, answer(con, cfg, text, lock), chat)
        except Exception as e:
            log(f"telegram: {e}")
            time.sleep(15)
