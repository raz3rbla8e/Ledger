"""Apple Pay taps → instant transactions. An iPhone Shortcuts automation ("When I tap <card>") POSTs
{"amount": "$12.34", "merchant": "Starbucks", "card": "Tangerine"} to /api/hook/applepay with header X-Ledger-Token.
The bank's email alert for the same purchase merges into it later, so nothing is counted twice."""
import re
from datetime import date, datetime

from .categorize import Categorizer
from .db import log_activity
from .store import insert


def ingest_tap(con, cfg, b):
    raw = str(b.get("amount", "")).replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", raw)
    if not m:
        raise ValueError("no amount")
    amt = abs(float(m.group(0)))
    if not 0 < amt < 20000:
        raise ValueError("amount out of range")  # iOS occasionally sends 0 / blank — never store junk
    card = str(b.get("card", "")).strip()
    cards = cfg.get("applepay", {}).get("cards", {})
    acct = next((v for k, v in cards.items() if k.lower() in card.lower()), None)
    if not acct:
        row = con.execute("SELECT id FROM accounts WHERE type='credit' ORDER BY id LIMIT 1").fetchone()
        acct = row["id"] if row else "applepay-card"
        con.execute("INSERT OR IGNORE INTO accounts(id, name, institution, type) VALUES (?,?,?,?)", (acct, card or "Apple Pay card", "Apple Pay", "credit"))
    merchant = re.sub(r"[\x00-\x1f]", "", str(b.get("merchant", "")))[:80].strip()
    blank = not merchant
    merchant = merchant or "Apple Pay purchase"
    d = str(b.get("date") or date.today().isoformat())[:10]
    if not re.match(r"\d{4}-\d\d-\d\d$", d):
        d = date.today().isoformat()
    tx = dict(account_id=acct, date=d, amount=-amt, description=merchant.upper(), source="push")
    tid = insert(con, Categorizer(con, cfg), tx, f"push:{acct}:{datetime.now().isoformat()}:{amt}", "credit", {})
    if tid and blank:
        con.execute("UPDATE transactions SET reviewed=0 WHERE id=?", (tid,))
    log_activity(con, "applepay", f"Apple Pay: ${amt:,.2f} at {merchant} logged instantly")
    con.commit()
    t = con.execute("SELECT category FROM transactions WHERE id=?", (tid,)).fetchone() if tid else None
    return {"ok": True, "id": tid, "category": t["category"] if t else None}
