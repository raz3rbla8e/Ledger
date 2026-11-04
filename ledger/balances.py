"""Account balances without bank logins.
A balance is known at an *anchor* (statement export with a balance column, OFX, or one number you typed once);
Ledger rolls it forward with every transaction it sees. Low-balance alert emails act as cross-checks."""
from datetime import date


def _anchor(con, acct, at=None):
    at = at or date.today().isoformat()
    return con.execute(
        "SELECT * FROM balance_anchors WHERE account_id=? AND date<=? ORDER BY date DESC, id DESC LIMIT 1", (acct, at)).fetchone()


def balance(con, acct, at=None):
    """(balance, info) — balance is None when Ledger has nothing to anchor on."""
    at = at or date.today().isoformat()
    a = _anchor(con, acct, at)
    approx = False
    if not a:
        h = con.execute("SELECT * FROM balance_hints WHERE account_id=? AND date<=? ORDER BY date DESC LIMIT 1", (acct, at)).fetchone()
        if not h:
            return None, dict(source=None)
        base, since, approx = h["max_balance"] / 2, h["date"], True
        src = f"low-balance alert {h['date']} (under ${h['max_balance']:,.0f})"
    else:
        base, since, src = a["balance"], a["date"], f"{a['source']} {a['date']}"
    moved = con.execute("SELECT COALESCE(sum(amount),0) FROM transactions WHERE account_id=? AND date>? AND date<=?",
                        (acct, since, at)).fetchone()[0]
    return round(base + moved, 2), dict(source=src, approx=approx, anchor_date=since)


def all_balances(con):
    out = []
    for a in con.execute("SELECT * FROM accounts WHERE COALESCE(hidden,0)=0 ORDER BY institution, type"):
        b, info = balance(con, a["id"])
        out.append(dict(id=a["id"], name=a["name"], institution=a["institution"], type=a["type"], balance=b, **info))
    return out


def cash_position(con):
    """Spendable cash = chequing/savings balances − credit card balances owed. Investments listed separately."""
    bs = all_balances(con)
    known = [b for b in bs if b["balance"] is not None]
    cash = sum(b["balance"] for b in known if b["type"] in ("chequing", "savings", "cash", "credit"))
    invest = sum(b["balance"] for b in known if b["type"] == "investment")
    # only accounts you actually use block the total; a barely-used account without a balance is just left out
    active = {r[0] for r in con.execute(
        "SELECT account_id FROM transactions WHERE date>=date('now','-90 day') GROUP BY account_id HAVING count(*)>=5")}
    missing = [b["name"] for b in bs if b["balance"] is None and b["type"] in ("chequing", "savings", "credit") and b["id"] in active]
    return dict(cash=round(cash, 2), investments=round(invest, 2), accounts=bs, missing=missing,
                complete=not missing and bool(known), approx=any(b.get("approx") for b in known))


def set_balance(con, acct, amount, when=None, source="manual"):
    con.execute("INSERT INTO balance_anchors(account_id, date, balance, source) VALUES (?,?,?,?)",
                (acct, when or date.today().isoformat(), float(amount), source))
    con.commit()


def drift_checks(con):
    """Low-balance alerts that contradict the estimate → the estimate is off (missing transactions or stale anchor)."""
    out = []
    for h in con.execute("SELECT * FROM balance_hints WHERE date>=date('now','-30 day')"):
        if not _anchor(con, h["account_id"], h["date"]):
            continue
        b, _ = balance(con, h["account_id"], h["date"])
        if b is not None and b > h["max_balance"] + 50:
            out.append(dict(account=h["account_id"], date=h["date"], estimate=b, max=h["max_balance"]))
    return out
