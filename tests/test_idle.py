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
r = parse_email("Tangerine Bank <catch@payments.interac.ca>", "Interac e-Transfer: The request for $25.00 transfer to PayDirect was successfully deposited.",
                "The $25.00 (CAD) you sent to PayDirect at x@y.com has been successfully deposited Reference Number: CAabc", "2026-08-11")
assert r and r[0]["amount"] == -25.0 and r[0]["account"][0] == "Tangerine" and r[0]["counterparty"] == "PAYDIRECT"
assert NOT_TXN_SENDER.search("Tangerine <forwardbanking@e.tangerine.ca>") and not NOT_TXN_SENDER.search("Tangerine <donotreply@tangerine.ca>")
assert NOT_TXN_SUBJECT.search("Your Credit Card payment will be due soon")
print("parsers OK")
