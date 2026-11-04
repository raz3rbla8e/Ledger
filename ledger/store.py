"""Insert with dedup, merge email alerts with statement rows, pair transfers."""
from datetime import date, timedelta

from .normalize import merchant_from_description, person_display


def _d(s):
    return date.fromisoformat(s)


def _shift(s, days):
    return (_d(s) + timedelta(days=days)).isoformat()


def has_ref(con, ref):
    return con.execute("SELECT 1 FROM refs WHERE ref=?", (ref,)).fetchone() is not None


def add_ref(con, ref, txn_id):
    con.execute("INSERT OR IGNORE INTO refs(ref, txn_id) VALUES (?,?)", (ref, txn_id))


def _find_twin(con, tx, want_email):
    """Find the same real-world transaction already stored from the other kind of source."""
    if want_email:  # statement row arriving → look for a pending email alert (alert date <= statement date)
        lo, hi = _shift(tx["date"], -7), _shift(tx["date"], 2)
        q = """SELECT t.* FROM transactions t WHERE t.account_id=? AND abs(t.amount-?)<0.005 AND t.date BETWEEN ? AND ?
               AND t.source IN ('email','push') AND t.status='pending' ORDER BY abs(julianday(t.date)-julianday(?)) LIMIT 1"""
    else:  # email arriving → look for a statement row that hasn't been matched to any email yet
        lo, hi = _shift(tx["date"], -2), _shift(tx["date"], 7)
        q = """SELECT t.* FROM transactions t WHERE t.account_id=? AND abs(t.amount-?)<0.005 AND t.date BETWEEN ? AND ?
               AND t.source IN ('csv','ofx') AND NOT EXISTS (SELECT 1 FROM refs r WHERE r.txn_id=t.id AND r.ref LIKE 'email:%')
               ORDER BY abs(julianday(t.date)-julianday(?)) LIMIT 1"""
    return con.execute(q, (tx["account_id"], tx["amount"], lo, hi, tx["date"])).fetchone()


def insert(con, cz, tx, ref, account_type=None, stats=None):
    """tx keys: account_id, date, amount, description, [counterparty, memo, merchant, source]. Returns txn id or None if dup."""
    stats = stats if stats is not None else {}
    if has_ref(con, ref):
        stats["dup"] = stats.get("dup", 0) + 1
        return None
    if tx.get("counterparty"):
        tx["merchant"] = person_display(tx["counterparty"])
    elif not tx.get("merchant"):
        tx["merchant"] = merchant_from_description(tx.get("description", ""))

    from_email = tx["source"] in ("email", "push")
    twin = _find_twin(con, tx, want_email=not from_email)
    if not twin and from_email:
        # Apple Pay push and the bank's email alert describe the same purchase
        other = "push" if tx["source"] == "email" else "email"
        twin = con.execute(
            """SELECT * FROM transactions WHERE account_id=? AND abs(amount-?)<0.005 AND source=? AND status='pending'
               AND date BETWEEN ? AND ? AND NOT EXISTS (SELECT 1 FROM refs r WHERE r.txn_id=transactions.id AND r.ref LIKE ?)
               ORDER BY abs(julianday(date)-julianday(?)) LIMIT 1""",
            (tx["account_id"], tx["amount"], other, _shift(tx["date"], -2), _shift(tx["date"], 2), f"{tx['source']}:%", tx["date"])).fetchone()
    if twin:
        if from_email:
            # statement already has it; enrich with the alert's nicer merchant / counterparty / memo
            con.execute(
                "UPDATE transactions SET counterparty=COALESCE(counterparty,?), memo=COALESCE(memo,?), updated_at=datetime('now') WHERE id=?",
                (tx.get("counterparty"), tx.get("memo"), twin["id"]),
            )
        else:
            # pending alert confirmed by statement
            con.execute(
                "UPDATE transactions SET status='posted', description=? || ' ⟂ ' || COALESCE(description,''), date=min(date, ?), updated_at=datetime('now') WHERE id=?",
                (tx.get("description", ""), tx["date"], twin["id"]),
            )
        add_ref(con, ref, twin["id"])
        stats["merged"] = stats.get("merged", 0) + 1
        return twin["id"]

    cz.categorize(tx, account_type)
    cur = con.execute(
        """INSERT INTO transactions(account_id, date, amount, currency, description, merchant, counterparty, memo,
           category, kind, cat_source, source, status, reviewed, ext_ref, confidence, tags)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tx["account_id"], tx["date"], round(tx["amount"], 2), tx.get("currency", "CAD"), tx.get("description"),
         tx.get("merchant"), tx.get("counterparty"), tx.get("memo"), tx["category"], tx["kind"], tx["cat_source"],
         tx["source"], "pending" if from_email else "posted", 0 if tx.get("_needs_review") else 1,
         tx.get("ext_ref"), tx.get("confidence"), tx.get("tags")),
    )
    add_ref(con, ref, cur.lastrowid)
    stats["new"] = stats.get("new", 0) + 1
    return cur.lastrowid


TRANSFER_CATS = ("Internal Transfer", "Credit Card Payment", "Investing")
LOOSE_CATS = ("Uncategorized", "Other Income", "Misc", "From People", "Paid to People")


def pair_transfers(con):
    """Link the two legs of money moving between your own accounts so it never counts as income or spending."""
    linked = 0
    # 1) exact: same Interac reference number on both sides
    for a in con.execute("""SELECT t.id, t.ext_ref, t.amount FROM transactions t WHERE t.link_id IS NULL AND t.ext_ref IS NOT NULL
                            AND t.kind='transfer' AND t.amount<0""").fetchall():
        b = con.execute("SELECT id FROM transactions WHERE ext_ref=? AND id!=? AND link_id IS NULL AND abs(amount + ?)<0.005",
                        (a["ext_ref"], a["id"], a["amount"])).fetchone()
        if b:
            con.execute("UPDATE transactions SET link_id=?, kind='transfer', category='Internal Transfer', reviewed=1 WHERE id=?", (b["id"], a["id"]))
            con.execute("UPDATE transactions SET link_id=?, kind='transfer', category='Internal Transfer', reviewed=1 WHERE id=?", (a["id"], b["id"]))
            linked += 1
    seeds = con.execute(
        "SELECT * FROM transactions WHERE link_id IS NULL AND kind='transfer' ORDER BY date"
    ).fetchall()
    for s in seeds:
        if con.execute("SELECT link_id FROM transactions WHERE id=?", (s["id"],)).fetchone()["link_id"]:
            continue
        q = f"""SELECT * FROM transactions WHERE link_id IS NULL AND id!=? AND account_id!=? AND abs(amount + ?)<0.005
                AND date BETWEEN ? AND ? AND (kind='transfer' OR (cat_source!='manual' AND category IN ({','.join('?'*len(LOOSE_CATS))})))
                ORDER BY kind='transfer' DESC, abs(julianday(date)-julianday(?)) LIMIT 1"""
        o = con.execute(q, (s["id"], s["account_id"], s["amount"], _shift(s["date"], -4), _shift(s["date"], 4), *LOOSE_CATS, s["date"])).fetchone()
        if not o:
            continue
        cat = s["category"] if s["category"] in TRANSFER_CATS else (o["category"] if o["category"] in TRANSFER_CATS else "Internal Transfer")
        con.execute("UPDATE transactions SET link_id=?, kind='transfer', category=?, reviewed=1 WHERE id=?", (o["id"], cat, s["id"]))
        con.execute(
            "UPDATE transactions SET link_id=?, kind='transfer', category=?, cat_source=CASE WHEN cat_source='manual' THEN 'manual' ELSE 'rule' END, reviewed=1 WHERE id=?",
            (s["id"], cat, o["id"]),
        )
        linked += 1
    con.commit()
    return linked
