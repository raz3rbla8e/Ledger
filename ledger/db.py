"""SQLite store. One file: ~/Ledger/ledger.db — open it with any SQLite tool if you want raw access."""
import sqlite3
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY,
  name TEXT, institution TEXT, type TEXT, last4 TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY,
  account_id TEXT REFERENCES accounts(id),
  date TEXT NOT NULL,                 -- YYYY-MM-DD
  amount REAL NOT NULL,               -- signed: + money in, - money out
  currency TEXT DEFAULT 'CAD',
  description TEXT,                   -- raw text from bank / email
  merchant TEXT,                      -- cleaned name
  counterparty TEXT,                  -- person, for e-transfers
  memo TEXT,                          -- e-transfer message etc
  category TEXT,
  kind TEXT,                          -- expense | income | transfer | reimbursement | refund
  cat_source TEXT,                    -- manual | rule | learned | default | ai | none
  source TEXT,                        -- email | csv | ofx | manual
  status TEXT DEFAULT 'posted',       -- pending (email only, awaiting statement) | posted
  link_id INTEGER,                    -- paired transfer leg
  note TEXT,
  reviewed INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS ix_tx_acct ON transactions(account_id, date);
CREATE INDEX IF NOT EXISTS ix_tx_merchant ON transactions(merchant);
CREATE TABLE IF NOT EXISTS refs (                -- every source id that maps to a transaction (dedup)
  ref TEXT PRIMARY KEY, txn_id INTEGER
);
CREATE TABLE IF NOT EXISTS rules (
  id INTEGER PRIMARY KEY,
  field TEXT DEFAULT 'merchant',      -- merchant | description | counterparty
  pattern TEXT NOT NULL,              -- case-insensitive; exact unless is_regex
  is_regex INTEGER DEFAULT 0,
  direction TEXT,                     -- NULL | in | out
  category TEXT, kind TEXT, rename TEXT,
  origin TEXT DEFAULT 'user',         -- user | ai
  hits INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS categories (
  name TEXT PRIMARY KEY, grp TEXT, kind TEXT, budget REAL, sort INTEGER
);
CREATE TABLE IF NOT EXISTS seen_emails (msg_id TEXT PRIMARY KEY, parsed INTEGER, seen_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS balance_anchors (            -- known balance on a date (statement, manual, inferred)
  id INTEGER PRIMARY KEY, account_id TEXT, date TEXT, balance REAL, source TEXT, created TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS balance_hints (              -- "balance is below $100" style signals
  id INTEGER PRIMARY KEY, account_id TEXT, date TEXT, max_balance REAL, source TEXT, UNIQUE(account_id, date, max_balance)
);
CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, target REAL, due TEXT, priority INTEGER DEFAULT 5, kind TEXT DEFAULT 'save',
  start_date TEXT DEFAULT (date('now')), note TEXT, active INTEGER DEFAULT 1, auto TEXT
);
CREATE TABLE IF NOT EXISTS activity (                   -- autopilot feed: what Ledger did on its own
  id INTEGER PRIMARY KEY, at TEXT DEFAULT (datetime('now')), kind TEXT, text TEXT, key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS sources (                    -- health of every intake channel
  key TEXT PRIMARY KEY, kind TEXT, last_ok TEXT, last_error TEXT, last_error_at TEXT, fail_streak INTEGER DEFAULT 0,
  last_item TEXT, items INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS unparsed (                   -- bank emails nothing could read (shown on System page)
  msg_id TEXT PRIMARY KEY, sender TEXT, subject TEXT, date TEXT, snippet TEXT, reason TEXT, resolved INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS outbox (key TEXT PRIMARY KEY, title TEXT, body TEXT, urgent INTEGER DEFAULT 0,
  created TEXT DEFAULT (datetime('now')), sent_at TEXT, channels TEXT);
CREATE TABLE IF NOT EXISTS sync_log (id INTEGER PRIMARY KEY, at TEXT DEFAULT (datetime('now')), summary TEXT);
"""

# (group, kind, [categories])
CATEGORY_TREE = [
    ("Income", "income", ["Salary", "Government & Benefits", "Interest", "Cashback & Rewards", "From People", "Other Income"]),
    ("Housing", "expense", ["Rent", "Utilities", "Internet & Phone", "Home Supplies"]),
    ("Food", "expense", ["Groceries", "Restaurants", "Coffee", "Food Delivery", "Alcohol"]),
    ("Transport", "expense", ["Gas", "Transit", "Rideshare", "Parking & Tolls", "Car Maintenance", "Car Insurance"]),
    ("Shopping", "expense", ["Clothing", "Electronics", "Online Shopping", "General Shopping"]),
    ("Subscriptions", "expense", ["Software & AI", "Streaming & Music"]),
    ("Health & Fitness", "expense", ["Gym", "Pharmacy", "Supplements", "Medical"]),
    ("Personal Care", "expense", ["Grooming"]),
    ("Fun", "expense", ["Entertainment", "Hobbies", "Games"]),
    ("Travel", "expense", ["Flights", "Hotels", "Travel Other"]),
    ("Education", "expense", ["Tuition & School", "Books"]),
    ("People & Giving", "expense", ["Paid to People", "Family Support", "Gifts", "Donations"]),
    ("Fees & Taxes", "expense", ["Bank Fees", "Interest Charges", "Taxes"]),
    ("Other", "expense", ["Uncategorized", "Misc"]),
    ("Transfers", "transfer", ["Internal Transfer", "Credit Card Payment", "Investing"]),
]


MIGRATIONS = [
    ("transactions", "tags", "TEXT"),
    ("transactions", "confidence", "REAL"),
    ("transactions", "ext_ref", "TEXT"),
    ("accounts", "hidden", "INTEGER DEFAULT 0"),
    ("accounts", "liquid", "INTEGER DEFAULT 1"),
    ("unparsed", "raw", "BLOB"),
]


def _migrate(con):
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    have = {r[0] for r in con.execute("SELECT name FROM categories")}
    extra = [("Shared Costs", "Housing", "expense"), ("Parking & Tolls", "Transport", "expense"), ("Line of Credit", "Transfers", "transfer"),
             ("Debt Repayment", "People & Giving", "expense"), ("Trip Spending", "Travel", "expense")]
    n = con.execute("SELECT COALESCE(max(sort),0) FROM categories").fetchone()[0]
    for name, grp, kind in extra:
        if have and name not in have:
            n += 1
            con.execute("INSERT INTO categories(name, grp, kind, sort) VALUES (?,?,?,?)", (name, grp, kind, n))


def connect(path):
    con = sqlite3.connect(path, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    if not con.execute("SELECT 1 FROM categories LIMIT 1").fetchone():
        i = 0
        for grp, kind, cats in CATEGORY_TREE:
            for c in cats:
                con.execute("INSERT INTO categories(name, grp, kind, sort) VALUES (?,?,?,?)", (c, grp, kind, i))
                i += 1
    _migrate(con)
    con.commit()
    return con


def ensure_account(con, acct_id, name=None, institution=None, type_=None, last4=None):
    row = con.execute("SELECT * FROM accounts WHERE id=?", (acct_id,)).fetchone()
    if row:
        return row
    con.execute(
        "INSERT INTO accounts(id, name, institution, type, last4) VALUES (?,?,?,?,?)",
        (acct_id, name or acct_id, institution, type_, last4),
    )
    return con.execute("SELECT * FROM accounts WHERE id=?", (acct_id,)).fetchone()


def category_kind(con, category):
    r = con.execute("SELECT kind FROM categories WHERE name=?", (category,)).fetchone()
    return r["kind"] if r else None


def now():
    return datetime.now().isoformat(timespec="seconds")


def log_activity(con, kind, text, key=None):
    """Record something Ledger did by itself. key de-duplicates (same key = logged once)."""
    con.execute("INSERT OR IGNORE INTO activity(kind, text, key) VALUES (?,?,?)", (kind, text, key))


def source_ok(con, key, kind, items=0, last_item=None):
    con.execute("""INSERT INTO sources(key, kind, last_ok, fail_streak, items, last_item) VALUES (?,?,datetime('now'),0,?,?)
                   ON CONFLICT(key) DO UPDATE SET last_ok=datetime('now'), fail_streak=0, items=items+excluded.items,
                   last_item=COALESCE(excluded.last_item, last_item)""", (key, kind, items, last_item))


def source_fail(con, key, kind, err):
    con.execute("""INSERT INTO sources(key, kind, last_error, last_error_at, fail_streak) VALUES (?,?,?,datetime('now'),1)
                   ON CONFLICT(key) DO UPDATE SET last_error=excluded.last_error, last_error_at=datetime('now'), fail_streak=fail_streak+1""",
                (key, kind, str(err)[:300]))
