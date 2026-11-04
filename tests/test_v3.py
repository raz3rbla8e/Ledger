"""v3 features: balance hints, balances + forecast + goals, Apple Pay merge, Downloads watcher, AI fallback, autopilot.
Run: python -m tests.test_v3"""
import json
import os
import re
import sys
import tempfile
import threading
import urllib.request
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ["LEDGER_HOME"] = tmp
os.environ["LEDGER_HOOK_TOKEN"] = "test-token-0123456789abcdef"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ledger import config, db  # noqa: E402
from ledger.categorize import Categorizer  # noqa: E402
from ledger.ingest_email import ingest_message  # noqa: E402

from ledger import demo_profile  # noqa: E402
cfg = demo_profile.apply(config.load())
cfg["ai"]["provider"] = "none"
dl = Path(tmp) / "Downloads"
dl.mkdir()
cfg["files"]["watch"] = [str(dl)]
con = db.connect(Path(tmp) / "ledger.db")
cz = Categorizer(con, cfg)
today = date.today()


def mail(sender, subj, body, d, auth=True):
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = sender, subj, f"<{abs(hash(subj + body + str(d)))}@t>"
    dom = re.search(r"@([\w.\-]+)", sender)
    if dom and auth:
        m["Authentication-Results"] = f"mx.google.com; dkim=pass header.i=@{dom.group(1)}; dmarc=pass (p=REJECT) header.from={dom.group(1)}"

    m["Date"] = f"{d:%a, %d %b %Y} 12:00:00 -0500"
    m.set_content(body)
    return m.as_bytes()


# 1) low-balance alert → hint, not a transaction
ingest_message(con, cfg, cz, mail("Tangerine <donotreply@tangerine.ca>", "Orange Alert: Low Account Balance",
               "Hi Jordan Avery, We're sending you this Orange Alert for your Tangerine Chequing Account ending in 4321, because the balance is below $100.00. To", today), {})
assert con.execute("select count(*) from transactions").fetchone()[0] == 0
h = con.execute("select * from balance_hints").fetchone()
assert h["account_id"] == "tangerine-4321" and h["max_balance"] == 100, dict(h)
print("hint OK")

# 2) a few months of history: pay, rent, dad, everyday spending
TANG = "Hi Jordan Avery, Just a quick note to let you know that a transaction of ${a:.2f} was made on your Tangerine Money-Back Credit Card 5360 xxxx xxxx 8888 at {m} on {d:%B} {d.day}, {d.year}."
WSF = "| This email was sent to you by Interac Corp., the owner of the Interac e-Transfer service, on behalf of Wealthsimple |"
start = today - timedelta(days=100)
d = start
i = 0
while d <= today:
    if (d - start).days % 14 == 0:
        ingest_message(con, cfg, cz, mail("Wealthsimple <notifications@o.wealthsimple.com>", "Direct deposit received",
                       "| Amount: $1,600.00 From: FORD MOTOR COMP Your deposit is now available in your chequing account. |", d), {})
    if d.day == 1:
        ingest_message(con, cfg, cz, mail("Wealthsimple <catch@payments.interac.ca>", "Interac e-Transfer: Your $1,050.00 transfer to PAT LANDLORD has been successfully deposited.",
                       f"The $1,050.00 (CAD) you sent to PAT LANDLORD has been successfully deposited.\nDate: {d:%b} {d.day}, {d.year}\n" + WSF, d), {})
    if d.day == 2:
        ingest_message(con, cfg, cz, mail("Wealthsimple <catch@payments.interac.ca>", "Interac e-Transfer: Dad has accepted your transfer of $750.00",
                       f"Date: {d:%b} {d.day}, {d.year}\n" + WSF, d), {})
    if d.weekday() in (1, 4):
        ingest_message(con, cfg, cz, mail("donotreply@tangerine.ca", "A new Credit Card transaction has been made", TANG.format(a=45 + i % 30, m="LOBLAWS #1032", d=d), d), {})
    i += 1
    d += timedelta(days=1)
con.commit()

from ledger.balances import cash_position, set_balance  # noqa: E402
from ledger.forecast import affordability, forecast  # noqa: E402
from ledger.goals import goals_status  # noqa: E402
from ledger.planner import safe_to_spend  # noqa: E402

f0 = forecast(con, cfg)
assert f0["relative"], "no balances yet → relative forecast"
for acct in [r["id"] for r in con.execute("select id from accounts")]:
    set_balance(con, acct, 0 if "cc" in acct else 3000 if "wealthsimple" in acct else 80)
pos = cash_position(con)
assert pos["complete"] and pos["cash"] > 0, pos
f = forecast(con, cfg)
assert not f["relative"] and f["months"], f["low"]
# co-op ends 2026-12-31 → no paycheques after it
after = [m for m in f["months"] if m["month"] > "2027-01"]
assert all(m["income"] < 1000 for m in after), after[:2]
# Dad paused in December, rent paused April 2027, rent stops May 2027
dec = next(m for m in f["months"] if m["month"] == "2026-12")
jan = next(m for m in f["months"] if m["month"] == "2027-01")
assert dec["fixed"] < jan["fixed"], (dec, jan)
may = next(m for m in f["months"] if m["month"] == "2027-05")
assert may["fixed"] < jan["fixed"] - 900, (may, jan)
print("forecast OK", f["low"], f["below_zero"])
s = safe_to_spend(con, cfg)
print("safe", {k: s[k] for k in ("safe_to_spend", "today_allowance", "hold_back", "hold_reason", "fixed_monthly")})
a = affordability(con, cfg, 300)
assert a["verdict"] in ("yes", "tight", "only_by_dipping", "no", "already_short")
g = goals_status(con, cfg, f, s)
names = [x["name"] for x in g["goals"]]
assert "Emergency fund" in names and "Pay Dad back (PS5)" in names, names
print("goals OK", [(x["name"], x["target"], x["funded"]) for x in g["goals"]])

# 3) Apple Pay tap then bank email for the same purchase → ONE transaction
from ledger.server import make_handler  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(con, cfg))
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]


def post(path, body, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **(headers or {})})
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        return {"status": e.code}


assert post("/api/hook/applepay", {"amount": "$6.45", "merchant": "Starbucks", "card": "Tangerine"}, {"X-Ledger-Token": "nope"})["status"] == 401
r = post("/api/hook/applepay", {"amount": "$6.45", "merchant": "Starbucks", "card": "Tangerine Money-Back"}, {"X-Ledger-Token": "test-token-0123456789abcdef"})
assert r["ok"] and r["category"] == "Coffee", r
ingest_message(con, cfg, cz, mail("donotreply@tangerine.ca", "A new Credit Card transaction has been made", TANG.format(a=6.45, m="STARBUCKS 04312", d=today), today), {})
con.commit()
n = con.execute("select count(*) from transactions where amount=-6.45").fetchone()[0]
assert n == 1, n
print("Apple Pay merge OK")

# 4) Downloads watcher: a Tangerine chequing export lands in Downloads
(dl / "Tangerine-transactions.csv").write_text("Date,Transaction,Name,Memo,Amount\n"
                                                f"{today:%m/%d/%Y},DEBIT,SHOPPERS DRUG MART #2145,,-23.50\n")
(dl / "random.csv").write_text("a,b,c\n1,2,3\n")
from ledger.ingest_files import watch_folders  # noqa: E402
st = watch_folders(con, cfg, Categorizer(con, cfg), log=lambda *_: None)
assert st.get("files") == 1, st
row = con.execute("select * from transactions where amount=-23.5").fetchone()
assert row["account_id"] == "tangerine-4321" and row["category"] == "Pharmacy", dict(row)
assert watch_folders(con, cfg, Categorizer(con, cfg), log=lambda *_: None) == {}  # not re-imported
print("watcher OK")

# 5) unknown bank email: no AI → parked in 'unparsed', AI → transaction
raw = mail("RBC Royal Bank <alerts@rbc.com>", "Your Visa purchase", "A purchase of $42.10 was made with your card. Thanks for banking with RBC.", today)
ingest_message(con, cfg, cz, raw, {})
assert con.execute("select count(*) from unparsed where resolved=0").fetchone()[0] == 0  # generic parser handles this one
raw = mail("RBC Royal Bank <alerts@rbc.com>", "Activity on your account", "We noticed $19.99 left your account for NETFLIX on Nov 1.", today)
ingest_message(con, cfg, cz, raw, {})
assert con.execute("select count(*) from unparsed where resolved=0").fetchone()[0] == 1
import ledger.ai as ai  # noqa: E402
ai.provider = lambda c: "ollama"
ai.complete = lambda c, p, **k: json.dumps({"is_transaction": True, "amount": 19.99, "direction": "out", "counterparty_or_merchant": "NETFLIX",
                                            "date": today.isoformat(), "bank": "RBC", "account_last4": None, "kind": "purchase"})
ingest_message(con, cfg, cz, raw, {})
con.commit()
t = con.execute("select * from transactions where amount=-19.99").fetchone()
assert t and t["category"] == "Streaming & Music" and t["reviewed"] == 0, t and dict(t)
assert con.execute("select count(*) from unparsed where resolved=0").fetchone()[0] == 0
print("AI fallback OK")

# 6) autopilot + notifications + pages don't crash
from ledger import api  # noqa: E402
from ledger.autopilot import run as autopilot  # noqa: E402
from ledger.notify import detect  # noqa: E402
autopilot(con, cfg, log=lambda *_: None)
detect(con, cfg)
for fn in (lambda: api.today_page(con, cfg, today.strftime("%Y-%m")), lambda: api.spending_page(con, cfg, today.strftime("%Y-%m")),
           lambda: api.plan_page(con, cfg), lambda: api.system_page(con, cfg)):
    json.dumps(fn(), default=str)
from ledger.assistant import builtin  # noqa: E402
for q in ("/today", "/cash", "/plan", "/goals", "can I afford $300?", "how much on groceries this month"):
    out = builtin(con, cfg, q)
    assert out, q
print(builtin(con, cfg, "can I afford $300?")[:200])
print("ALL V3 TESTS PASSED")
