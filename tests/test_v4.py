"""v4: security (host/auth/CSRF/tokens/headers), secrets store, people amount rules, split monthly transfers,
what-if scenarios, widget, backups, MCP server. Run: python -m tests.test_v4"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ["LEDGER_HOME"] = tmp
os.environ["LEDGER_NO_KEYCHAIN"] = "1"  # never touch the real OS keychain from tests
for k in ("LEDGER_DASHBOARD_PASSWORD", "LEDGER_WIDGET_TOKEN", "LEDGER_HOOK_TOKEN"):
    os.environ.pop(k, None)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ledger import config, db, demo_profile, secrets_store  # noqa: E402
from ledger.categorize import Categorizer  # noqa: E402
from ledger.store import insert  # noqa: E402

cfg = demo_profile.apply(config.load())
con = db.connect(Path(tmp) / "ledger.db")
db.ensure_account(con, "ws", "Wealthsimple Chequing", "Wealthsimple", "chequing")
db.ensure_account(con, "tg", "Tangerine Chequing", "Tangerine", "chequing")
cz = Categorizer(con, cfg)
today = date.today()

# ── secrets: .env fallback when no keychain, never echoes ──
env = Path(tmp) / ".env"
assert secrets_store.set("LEDGER_WIDGET_TOKEN", "w" * 32, env) == ".env file"
assert "LEDGER_WIDGET_TOKEN=" + "w" * 32 in env.read_text()
assert secrets_store.get("LEDGER_WIDGET_TOKEN") == "w" * 32
print("secrets OK")

# ── people rules by amount: roommate's $800 = rent paid back, other amounts = shared groceries ──
t1 = dict(account_id="tg", date=today.isoformat(), amount=800.0, description="INTERAC E-TRANSFER FROM SAM ROOMIE", counterparty="SAM ROOMIE", source="email")
t2 = dict(account_id="tg", date=today.isoformat(), amount=46.20, description="INTERAC E-TRANSFER FROM SAM ROOMIE", counterparty="SAM ROOMIE", source="email")
i1, i2 = insert(con, cz, t1, "t:1"), insert(con, cz, t2, "t:2")
r = {x["id"]: x for x in con.execute("select * from transactions")}
assert r[i1]["category"] == "Rent" and r[i1]["kind"] == "reimbursement", dict(r[i1])
assert r[i2]["category"] == "Groceries" and r[i2]["kind"] == "reimbursement", dict(r[i2])
print("people amount rules OK")

# ── Dad paid in two transfers a month → still recognised as a monthly obligation ──
for k in range(1, 5):
    mo = (today.replace(day=1) - timedelta(days=28 * k)).replace(day=1)
    insert(con, cz, dict(account_id="ws", date=mo.replace(day=3).isoformat(), amount=-900.0, description="E-TRANSFER TO DAD", counterparty="DAD", source="email"), f"d{k}a")
    insert(con, cz, dict(account_id="ws", date=mo.replace(day=18).isoformat(), amount=-950.0 - k * 10, description="E-TRANSFER TO DAD", counterparty="DAD", source="email"), f"d{k}b")
con.commit()
from ledger import insights  # noqa: E402
rec = [x for x in insights.recurring(con) if x["merchant"] == "Dad"]
assert rec and rec[0]["cadence"] == "monthly" and 1800 <= rec[0]["amount"] <= 1900, rec
print("split monthly transfers OK", rec[0]["amount"])

# ── what-if scenario raises the forecast ──
from ledger.api import scenario_from_query  # noqa: E402
from ledger.balances import set_balance  # noqa: E402
from ledger.forecast import forecast  # noqa: E402
set_balance(con, "ws", 5000)
set_balance(con, "tg", 100)
base = forecast(con, cfg)
sc = forecast(con, cfg, scenario=scenario_from_query({"job_monthly": "4000", "job_start": "2027-05", "osap": "6000"}))
assert sc["end"] > base["end"] + 20000, (base["end"], sc["end"])
print("scenarios OK")

# ── widget payload + backup ──
from ledger.backup import nightly  # noqa: E402
from ledger.widget import widget_payload  # noqa: E402
w = widget_payload(con, cfg)
assert set(w) >= {"today", "left_month", "days_left", "tone"} and "transactions" not in json.dumps(w)
assert nightly(con, cfg, log=lambda *_: None).exists() and nightly(con, cfg, log=lambda *_: None) is None
print("widget + backup OK")

# ── HTTP security ──
from http.server import ThreadingHTTPServer  # noqa: E402
from ledger.server import make_handler, serve  # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(con, cfg))
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]


def req(path, method="GET", headers=None, body=None):
    rq = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, headers=headers or {},
                                data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(rq) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


s, h, _ = req("/api/version")
assert s == 200 and h["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in h["Content-Security-Policy"]
assert req("/api/version", headers={"Host": "evil.example.com"})[0] == 421                       # DNS rebinding blocked
assert req("/api/version", headers={"Host": "laptop.tail1234.ts.net"})[0] == 403                # remote, no password set → off
os.environ["LEDGER_DASHBOARD_PASSWORD"] = "correct horse battery"
assert req("/api/version", headers={"Host": "laptop.tail1234.ts.net"})[0] == 401
good = "Basic " + base64.b64encode(b"me:correct horse battery").decode()
assert req("/api/version", headers={"Host": "laptop.tail1234.ts.net", "Authorization": good})[0] == 200
assert req("/api/review/ok", "POST", {"content-type": "application/json"}, {})[0] == 403        # CSRF header missing
assert req("/api/review/ok", "POST", {"content-type": "application/json", "X-Ledger-Req": "1", "Origin": "https://evil.com"}, {})[0] == 403
assert req("/api/review/ok", "POST", {"content-type": "application/json", "X-Ledger-Req": "1"}, {})[0] == 200
assert req("/api/widget")[0] == 401                                                              # token required
s, _, b = req("/api/widget", headers={"X-Ledger-Token": "w" * 32})
assert s == 200 and "today" in json.loads(b)
assert req("/api/hook/applepay", "POST", {"X-Ledger-Token": "x"}, {"amount": "1"})[0] == 401     # hook off without token
os.environ["LEDGER_HOOK_TOKEN"] = "h" * 32
assert req("/api/hook/applepay", "POST", {"X-Ledger-Token": "h" * 32}, {"amount": "$0.00", "merchant": ""})[0] == 400  # junk tap rejected
for _ in range(9):
    req("/api/version", headers={"Host": "laptop.tail1234.ts.net", "Authorization": "Basic " + base64.b64encode(b"me:wrong").decode()})
assert req("/api/version", headers={"Host": "laptop.tail1234.ts.net", "Authorization": good})[0] == 429  # lockout after repeated failures
os.environ.pop("LEDGER_DASHBOARD_PASSWORD")
try:
    serve(con, cfg, "0.0.0.0", 0)
    raise AssertionError("should refuse to bind publicly without a password")
except SystemExit:
    pass
print("HTTP security OK")

# ── MCP server (stdio, read-only) ──
p = subprocess.Popen([sys.executable, "-m", "ledger", "mcp"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                     env={**os.environ, "LEDGER_HOME": tmp})
msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search_transactions", "arguments": {"query": "DAD", "limit": 3}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "can_i_afford", "arguments": {"amount": 250}}}]
out, _ = p.communicate("\n".join(json.dumps(m) for m in msgs) + "\n", timeout=60)
res = {r["id"]: r for r in map(json.loads, out.strip().splitlines())}
assert res[1]["result"]["serverInfo"]["name"] == "ledger"
names = {t["name"] for t in res[2]["result"]["tools"]}
assert "can_i_afford" in names and not any(w in n for n in names for w in ("delete", "update", "send", "write"))
assert len(json.loads(res[3]["result"]["content"][0]["text"])) == 3
assert "verdict" in json.loads(res[4]["result"]["content"][0]["text"])
print("MCP OK")
print("ALL V4 TESTS PASSED")
