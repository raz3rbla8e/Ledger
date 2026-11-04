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
