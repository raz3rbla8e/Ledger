"""IMAP IDLE survives quiet periods (fresh connection after a read timeout) and reports new mail."""
import imaplib, socket, threading, time, sys
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
from ledger import ingest_email as ie
ie.IDLE_SECONDS = 1
conns = []
def server(sock):
    while True:
        c, _ = sock.accept(); conns.append(1)
        def h(c=c, n=len(conns)):
            f = c.makefile("rb"); c.sendall(b"* OK fake\r\n")
            while True:
                ln = f.readline()
                if not ln: return
                tag = ln.split()[0]
                if b"CAPABILITY" in ln:
                    c.sendall(b"* CAPABILITY IMAP4rev1 IDLE\r\n" + tag + b" OK done\r\n")
                elif b"IDLE" in ln:
                    c.sendall(b"+ idling\r\n")
                    if n == 2:  # second connection: mail arrives
                        time.sleep(0.3); c.sendall(b"* 7 EXISTS\r\n")
                elif b"DONE" in ln:
                    c.sendall(last + b" OK idle done\r\n")
                elif b"LOGOUT" in ln:
                    c.sendall(b"* BYE\r\n" + tag + b" OK done\r\n"); return
                last = tag
        threading.Thread(target=h, daemon=True).start()
s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(); port = s.getsockname()[1]
threading.Thread(target=server, args=(s,), daemon=True).start()
ie._open = lambda ib, pw: (imaplib.IMAP4("127.0.0.1", port), True)
hits, logs, stop = [], [], threading.Event()
t = threading.Thread(target=ie.idle_forever, args=({"address": "x"}, "pw", lambda: hits.append(1), logs.append, stop), daemon=True)
t.start(); time.sleep(3.5); stop.set()
print("connections", len(conns), "mail hits", len(hits), "logs", logs)
assert len(conns) >= 2 and len(hits) >= 1 and not logs
print("IDLE OK")

# ── batched Message-ID fetch + resumable backfill ──
d = [(b"1 (UID 101 BODY[HEADER.FIELDS (MESSAGE-ID)] {22}", b"Message-ID: <a@x>\r\n\r\n"), b")",
     (b"2 (BODY[HEADER.FIELDS (MESSAGE-ID)] {22}", b"Message-ID: <b@x>\r\n\r\n"), b" UID 102)"]
class FM:
    def uid(self, *a):
        return "OK", d
assert ie._message_ids(FM(), [101, 102]) == {101: "<a@x>", 102: "<b@x>"}
print("batch fetch OK")

import os, tempfile
os.environ["LEDGER_HOME"] = tempfile.mkdtemp(); os.environ["LEDGER_NO_KEYCHAIN"] = "1"
from ledger import config, db
from ledger.categorize import Categorizer
cfg = config.load(); con = db.connect(cfg["_home"] / "ledger.db"); cz = Categorizer(con, cfg)
UIDS = list(range(1, 451))
body = lambda u: (f"Message-ID: <m{u}@x>\r\nFrom: alerts@tangerine.ca\r\nSubject: hello\r\nDate: Mon, 2 Nov 2026 10:00:00 -0500\r\n\r\nnothing\r\n").encode()
fetched = []
class FakeIMAP:
    def response(self, k): return "OK", [b"7"]
    def logout(self): pass
    def uid(self, cmd, *a):
        if cmd == "SEARCH":
            lo = int(a[1].split(b":" if isinstance(a[1], bytes) else ":")[0]) if a[0] == "UID" else 1
            return "OK", [" ".join(str(u) for u in UIDS if u >= lo).encode()]
        ids, what = a
        if "MESSAGE-ID" in what:
            out = []
            for u in map(int, ids.split(",")):
                out += [(f"{u} (UID {u} BODY[HEADER.FIELDS (MESSAGE-ID)] {{20}}".encode(), f"Message-ID: <m{u}@x>\r\n\r\n".encode()), b")"]
            return "OK", out
        fetched.append(int(ids)); return "OK", [(b"x", body(int(ids))), b")"]
ie._open = lambda ib, pw: (FakeIMAP(), True)
ib = {"address": "me@gmail.com", "password_env": "X", "imap_host": "imap.gmail.com"}
stats = {}
ie._sync_inbox(con, cfg, cz, ib, "pw", stats, lambda *a: None)
assert len(fetched) == 450 and con.execute("SELECT value FROM meta WHERE key='email_uid:me@gmail.com'").fetchone()[0] == "7:450"
fetched.clear(); UIDS.append(451)
ie._sync_inbox(con, cfg, cz, ib, "pw", stats, lambda *a: None)
assert fetched == [451], fetched  # only the new one
print("backfill OK")

# ── money requests you paid, and bank marketing that isn't a transaction ──
from ledger.ingest_email import parse_email, NOT_TXN_SENDER, NOT_TXN_SUBJECT
r = parse_email("Tangerine Bank <catch@payments.interac.ca>", "Interac e-Transfer: The request for $25.00 transfer to BetSite Pay was successfully deposited.",
                "The $25.00 (CAD) you sent to BetSite Pay at x@y.com has been successfully deposited Reference Number: CAabc", "2026-08-11")
assert r and r[0]["amount"] == -25.0 and r[0]["account"][0] == "Tangerine" and r[0]["counterparty"] == "BETSITE PAY"
assert NOT_TXN_SENDER.search("Tangerine <forwardbanking@e.tangerine.ca>") and not NOT_TXN_SENDER.search("Tangerine <donotreply@tangerine.ca>")
assert NOT_TXN_SUBJECT.search("Your Credit Card payment will be due soon")
print("parsers OK")

# ── people rules for a betting site: deposits and withdrawals net out in one category ──
from ledger.categorize import Categorizer, recategorize_all
cfg["people"] = [{"name": "BETSITE PAY", "incoming": {"category": "Gambling"}, "outgoing": {"category": "Gambling"}}]
cz2 = Categorizer(con, cfg)
out = cz2.categorize({"amount": -20.0, "counterparty": "BETSITE PAY", "description": "INTERAC E-TRANSFER TO BETSITE PAY"})
inn = cz2.categorize({"amount": 35.0, "counterparty": "BETSITE PAY", "description": "INTERAC E-TRANSFER FROM BETSITE PAY"})
assert (out["category"], out["kind"]) == ("Gambling", "expense") and (inn["category"], inn["kind"]) == ("Gambling", "reimbursement"), (out, inn)
print("gambling OK")

# ── config.toml edits apply live; a broken file keeps the last good settings ──
from ledger.sync import apply_config_changes
p = cfg["_home"] / "config.toml"
apply_config_changes(con, cfg, lambda *a: None, reload=True)  # baseline
tx = {"amount": -80.0, "counterparty": "PAT PARKINGSPOT", "description": "INTERAC E-TRANSFER TO PAT PARKINGSPOT", "account_id": "x",
      "date": "2026-10-24", "source": "email"}
con.execute("INSERT INTO accounts(id, name, institution, type) VALUES ('x','X','Tangerine','chequing')")
con.execute("INSERT INTO transactions(account_id, date, amount, description, counterparty, category, kind, cat_source, source, status, reviewed) "
            "VALUES ('x','2026-10-24',-80,'INTERAC E-TRANSFER TO PAT PARKINGSPOT','PAT PARKINGSPOT','Paid to People','expense','default','email','pending',0)")
con.commit()
p.write_text(p.read_text() + '\n[[people]]\nname = "PAT PARKINGSPOT"\noutgoing = { category = "Parking & Tolls" }\n')
logs = []
assert apply_config_changes(con, cfg, logs.append, reload=True)
r_ = tuple(con.execute("SELECT category, reviewed FROM transactions WHERE counterparty='PAT PARKINGSPOT'").fetchone()); assert r_ == ("Parking & Tolls", 1), r_
assert any(pp["name"] == "PAT PARKINGSPOT" for pp in cfg["people"])
good = p.read_text(); p.write_text(good + "\nthis is = = broken\n")
assert not apply_config_changes(con, cfg, logs.append, reload=True) and cfg["people"] and "error" in logs[-1]
n = len(logs); apply_config_changes(con, cfg, logs.append, reload=True); assert len(logs) == n  # reported once
print("config reload OK")

# ── friends you trade money with net out; known people don't need review ──
con.execute("DELETE FROM transactions"); con.commit()
def add(cp, amt, d):
    con.execute("INSERT INTO transactions(account_id, date, amount, description, counterparty, category, kind, cat_source, source, status, reviewed) "
                "VALUES ('x',?,?,?,?,'Uncategorized','expense','none','email','pending',0)", (d, amt, f"INTERAC E-TRANSFER {cp}", cp))
for i, a in enumerate([-30, 15, -12, 20]):
    add("JACOB EXAMPLE", a, f"2026-09-0{i+1}")
add("STRANGER PERSON", 900, "2026-09-09")
add("ALEX FRIEND", 25, "2026-09-10")
add("SOME CO INC.", 50, "2026-09-11")
con.commit()
cfg["people"] = []
recategorize_all(con, cfg)
rows = {(r[0], r[1]): r[2:] for r in con.execute("SELECT counterparty, amount, category, kind, reviewed FROM transactions")}
assert rows[("JACOB EXAMPLE", 15)] == ("Paid to People", "reimbursement", 1), rows
assert rows[("JACOB EXAMPLE", -12)] == ("Paid to People", "expense", 1)
assert rows[("STRANGER PERSON", 900)] == ("From People", "income", 0)  # a big sum from someone new: worth a look
assert rows[("ALEX FRIEND", 25)] == ("Paid to People", "reimbursement", 1)     # small payback from a friend nets out
assert rows[("SOME CO INC.", 50)] == ("From People", "income", 0)          # a company paying you is income
print("people OK")

# ── [[merchants]] in config.toml ──
cfg["merchants"] = [{"match": "1234567 CANADA", "category": "Restaurants", "rename": "Burger place (probably)"}]
t = Categorizer(con, cfg).categorize({"amount": -13.5, "description": "1234567 CANADA INC.", "merchant": "1234567 Canada"})
assert (t["category"], t["merchant"], t["_needs_review"]) == ("Restaurants", "Burger place (probably)", False), t
print("merchants OK")

# ── re-sorting keeps "money you moved from RBC = government money" ──
cfg["me"]["names"] = ["JORDAN EXAMPLE"]
cfg["me"]["income_when_moved_from"] = {"RBC": "Government & Benefits"}
con.execute("DELETE FROM transactions")
con.execute("INSERT INTO transactions(account_id, date, amount, description, counterparty, category, kind, cat_source, source, status, reviewed) "
            "VALUES ('x','2026-09-01',496,'INTERAC E-TRANSFER FROM JORDAN EXAMPLE (RBC)','JORDAN EXAMPLE','Government & Benefits','income','rule','email','pending',1)")
con.commit(); recategorize_all(con, cfg)
assert con.execute("SELECT category FROM transactions").fetchone()[0] == "Government & Benefits"
print("self-transfer source OK")

# ── e-transfers to a company are sorted by what the company is ──
t = Categorizer(con, cfg).categorize({"amount": -3700.0, "counterparty": "EXAMPLE REALTY ULC", "description": "INTERAC E-TRANSFER TO EXAMPLE REALTY ULC"})
assert t["category"] == "Rent", t
print("company payee OK")

# ── Gmail Takeout export (.zip with an .mbox) goes through the normal email pipeline ──
import zipfile as _zf, tempfile as _tf
from ledger.ingest_mbox import import_mail_export, is_mail_export
_d = _tf.mkdtemp()
_m = ("From 123@xxx Mon Nov 02 10:00:00 +0000 2026\nMessage-ID: <exp1@x>\nFrom: Wealthsimple <notifications@o.wealthsimple.com>\n"
      "Authentication-Results: mx.google.com; dkim=pass header.i=@o.wealthsimple.com; dmarc=pass header.from=wealthsimple.com\n"
      "Subject: Direct deposit received\nDate: Mon, 2 Nov 2026 10:00:00 -0500\n\nAmount: $881.57 From: EXAMPLE EMPLOYER\n\n"
      "From 124@xxx Mon Nov 02 10:00:00 +0000 2026\nMessage-ID: <exp2@x>\nFrom: Someone <me@example.com>\nSubject: hi\n\nnot a bank\n")
_zp = os.path.join(_d, "takeout.zip")
with _zf.ZipFile(_zp, "w") as z:
    z.writestr("Takeout/Mail/Ledger.mbox", _m)
assert is_mail_export(_zp)
st = import_mail_export(con, cfg, Categorizer(con, cfg), _zp, lambda *a: None)
assert st["messages"] == 2 and st["skipped"] == 1, st
_r = con.execute("SELECT amount, category FROM transactions WHERE description LIKE '%EXAMPLE EMPLOYER%'").fetchone(); assert _r and tuple(_r) == (881.57, "Salary"), (tuple(_r) if _r else None, st)
st = import_mail_export(con, cfg, Categorizer(con, cfg), _zp, lambda *a: None)
assert st.get("new", 0) == 0  # importing twice adds nothing
print("mail export OK")

# ── pins, told fixed costs, one-off lumps ──
from ledger import planner
cfg["pin"] = [{"date": "2026-10-29", "amount": 540, "who": "ROOMIE", "category": "Rent"}]
t = Categorizer(con, cfg).categorize({"date": "2026-10-29", "amount": 540.0, "counterparty": "ROOMIE", "description": "INTERAC E-TRANSFER FROM ROOMIE"})
assert (t["category"], t["kind"]) == ("Rent", "reimbursement"), t
cfg["plan"]["fixed"] = {"Rent": 1050}
assert planner.fixed_costs(con, cfg)["Rent"] == {"amount": 1050.0, "how": "you set it"}
from datetime import date as _dd
con.execute("DELETE FROM transactions")
for d, a in (("2026-08-10", 90), ("2026-09-08", 2350), ("2026-09-20", 60), ("2026-10-04", 160), ("2026-10-09", 90)):
    con.execute("INSERT INTO transactions(account_id, date, amount, description, category, kind, cat_source, source, status, reviewed) "
                "VALUES ('x',?,?,'GOVT','Government & Benefits','income','rule','email','pending',1)", (d, a))
con.commit()
assert planner.other_income_monthly(con, _dd(2026, 11, 4)) == 90.0  # medians of 90, 60, 250 — the $2,350 lump is a one-off
print("pins/fixed/lumps OK")
