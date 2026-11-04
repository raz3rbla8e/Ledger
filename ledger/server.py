"""Local dashboard: http://127.0.0.1:8765  (only reachable from this computer unless you pass --host 0.0.0.0)."""
import base64
import hmac
import json
import os
import mimetypes
import threading
import time
import traceback
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import insights as ins
from .categorize import recategorize_all
from .store import pair_transfers
from .sync import run_sync

STATIC = Path(__file__).parent / "static"
LOCK = threading.RLock()
_FAILS = {}


def _fail(ip):
    now = time.time()
    _FAILS[ip] = [t for t in _FAILS.get(ip, []) if now - t < 900] + [now]


def _locked(ip):
    now = time.time()
    return len([t for t in _FAILS.get(ip, []) if now - t < 900]) >= 8


def make_handler(con, cfg):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        # ───── security ─────
        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                             "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            super().end_headers()

        def _hostname(self):
            h = (self.headers.get("Host") or "").strip().lower()
            if h.startswith("["):
                return h.split("]")[0] + "]"
            return h.rsplit(":", 1)[0] if ":" in h else h

        def _host_ok(self):
            # blocks DNS-rebinding: a random website can't make your browser talk to Ledger under its own name
            h = self._hostname()
            allowed = {"localhost", "127.0.0.1", "[::1]"} | {x.lower() for x in cfg["server"].get("allowed_hosts", [])}
            return h in allowed or h.endswith(".ts.net")

        def _is_local(self):
            return self.client_address[0] in ("127.0.0.1", "::1") and self._hostname() in ("localhost", "127.0.0.1", "[::1]")

        def _deny(self, code, msg, extra=None):
            b = msg.encode()
            self.send_response(code)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return False

        def _authed(self):
            if not self._host_ok():
                return self._deny(421, "Unknown host")
            if self._is_local():
                return True  # this computer
            pw = os.environ.get("LEDGER_DASHBOARD_PASSWORD")
            if not pw:
                return self._deny(403, "Remote access is off. Set a dashboard password with `python -m ledger setup`.")
            ip = self.client_address[0]
            if _locked(ip):
                return self._deny(429, "Too many wrong passwords — try again in 15 minutes.")
            h = self.headers.get("Authorization", "")
            try:
                given = base64.b64decode(h[6:]).decode().split(":", 1)[1] if h.startswith("Basic ") else ""
            except Exception:
                given = ""
            if given and hmac.compare_digest(given, pw):
                return True
            if given:
                _fail(ip)
            return self._deny(401, "Login required", {"WWW-Authenticate": 'Basic realm="Ledger", charset="UTF-8"'})

        def _csrf_ok(self):
            # browsers can't add custom headers to cross-site form posts, so this header proves the request came from Ledger's own page
            if self.headers.get("X-Ledger-Req") != "1":
                return self._deny(403, "Missing request header")
            o = self.headers.get("Origin")
            if o and urlparse(o).hostname not in ("127.0.0.1", "localhost", self._hostname()):
                return self._deny(403, "Cross-origin request blocked")
            return True

        def _json(self, obj, code=200):
            b = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def _bump(self):
            con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('data_version', COALESCE((SELECT value FROM meta WHERE key='data_version'),0)+1)")
            con.commit()

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 200_000:
                raise ValueError("request too large")
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_GET(self):
            u0 = urlparse(self.path)
            if u0.path == "/api/widget":
                return self._widget(u0)
            if not self._authed():
                return
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path in ("/", "/index.html"):
                    return self._file(STATIC / "index.html")
                if u.path.startswith("/static/"):
                    return self._file(STATIC / u.path[len("/static/"):])
                with LOCK:
                    return self._api_get(u.path, q)
            except Exception as e:
                traceback.print_exc()
                return self._json({"error": str(e)}, 500)

        def _file(self, p: Path):
            p = p.resolve()
            if STATIC.resolve() not in p.parents and p != STATIC.resolve() or not p.is_file():
                self.send_error(404)
                return
            b = p.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(p))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _api_get(self, path, q):
            month = q.get("month") or date.today().strftime("%Y-%m")
            from . import api as pages
            if path == "/api/version":
                return self._json({"version": pages._version(con)})
            if path == "/api/today":
                return self._json(pages.today_page(con, cfg, month))
            if path == "/api/spending":
                return self._json(pages.spending_page(con, cfg, month))
            if path == "/api/planpage":
                return self._json(pages.plan_page(con, cfg, q))
            if path == "/api/system":
                return self._json(pages.system_page(con, cfg))
            if path == "/api/overview":
                last = con.execute("SELECT at, summary FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
                return self._json(dict(
                    kpis=ins.kpis(con, month), monthly=ins.monthly(con, 12), categories=ins.by_category(con, month),
                    trend=ins.group_trend(con, 12), daily=ins.daily_cumulative(con, month), merchants=ins.top_merchants(con, month),
                    recurring=ins.recurring(con), flags=ins.anomalies(con, month), people=ins.people(con, cfg),
                    last_sync=dict(last) if last else None,
                    months=[r["m"] for r in con.execute("SELECT DISTINCT substr(date,1,7) m FROM transactions ORDER BY m DESC")],
                ))
            if path == "/api/plan":
                from . import planner
                return self._json(dict(safe=planner.safe_to_spend(con, cfg), budgets=planner.budgets(con, cfg, month),
                                       savings=planner.savings(con, cfg), goal_pct=cfg["plan"]["savings_goal_pct"],
                                       outbox=[dict(r) for r in con.execute("SELECT key, title, body, created, sent_at, channels FROM outbox ORDER BY created DESC LIMIT 15")]
                                       if con.execute("SELECT name FROM sqlite_master WHERE name='outbox'").fetchone() else []))
            if path == "/api/transactions":
                where, args = ["1=1"], []
                if q.get("month"):
                    where.append("substr(t.date,1,7)=?"); args.append(q["month"])
                for f in ("category", "account_id", "kind", "merchant", "counterparty"):
                    if q.get(f):
                        where.append(f"t.{f}=?"); args.append(q[f])
                if q.get("group"):
                    where.append("c.grp=?"); args.append(q["group"])
                if q.get("review"):
                    where.append("t.reviewed=0")
                if q.get("q"):
                    where.append("(t.description LIKE ? OR t.merchant LIKE ? OR t.note LIKE ? OR t.counterparty LIKE ? OR t.memo LIKE ?)")
                    args += [f"%{q['q']}%"] * 5
                rows = con.execute(
                    f"""SELECT t.*, a.name account_name, c.grp FROM transactions t LEFT JOIN accounts a ON a.id=t.account_id
                        LEFT JOIN categories c ON c.name=t.category WHERE {' AND '.join(where)} ORDER BY t.date DESC, t.id DESC LIMIT ?""",
                    (*args, int(q.get("limit", 500)))).fetchall()
                return self._json([dict(r) for r in rows])
            if path == "/api/meta":
                return self._json(dict(
                    categories=[dict(r) for r in con.execute("SELECT * FROM categories ORDER BY sort")],
                    accounts=ins.accounts(con, cfg),
                    rules=[dict(r) for r in con.execute("SELECT * FROM rules ORDER BY origin, id DESC")],
                    inbox=str(cfg["_inbox"]),
                ))
            return self._json({"error": "not found"}, 404)

        def do_POST(self):
            if urlparse(self.path).path == "/api/hook/applepay":
                return self._applepay()
            if not self._authed() or not self._csrf_ok():
                return
            u = urlparse(self.path)
            try:
                b = self._body()
                with LOCK:
                    return self._api_post(u.path, b)
            except Exception as e:
                traceback.print_exc()
                return self._json({"error": str(e)}, 500)

        def do_DELETE(self):
            if not self._authed() or not self._csrf_ok():
                return
            parts = urlparse(self.path).path.strip("/").split("/")
            with LOCK:
                if parts[:2] == ["api", "rules"]:
                    con.execute("DELETE FROM rules WHERE id=?", (int(parts[2]),))
                    con.commit()
                    return self._json({"recategorized": recategorize_all(con, cfg)})
                if parts[:2] == ["api", "tx"]:
                    con.execute("DELETE FROM transactions WHERE id=? AND source='manual'", (int(parts[2]),))
                    con.commit()
                    return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)

        def _token_ok(self, env_name, supplied):
            tok = os.environ.get(env_name, "")
            ip = self.client_address[0]
            if not self._host_ok():
                return self._deny(421, "Unknown host")
            if _locked(ip):
                return self._deny(429, "Locked out")
            if not tok or len(tok) < 16 or not hmac.compare_digest(supplied or "", tok):
                _fail(ip)
                return self._deny(401, "Bad token")
            return True

        def _widget(self, u):
            """Read-only, minimal numbers for the phone widget. Its own token, so the widget can't do anything else."""
            q = parse_qs(u.query)
            if not self._token_ok("LEDGER_WIDGET_TOKEN", self.headers.get("X-Ledger-Token") or (q.get("t") or [""])[0]):
                return
            with LOCK:
                from .widget import widget_payload
                return self._json(widget_payload(con, cfg))

        def _applepay(self):
            if not self._token_ok("LEDGER_HOOK_TOKEN", self.headers.get("X-Ledger-Token", "")):
                return
            try:
                b = self._body()
                from .applepay import ingest_tap
                with LOCK:
                    r = ingest_tap(con, cfg, b)
                    self._bump()
                return self._json(r)
            except (ValueError, KeyError, TypeError) as e:
                return self._json({"error": str(e)}, 400)

        def _api_post(self, path, b):
            parts = path.strip("/").split("/")
            if path == "/api/sync":
                logs = []
                s = run_sync(con, cfg, logs.append)
                return self._json({"summary": s, "log": logs})
            if parts[:2] == ["api", "tx"] and len(parts) == 3:
                tid = int(parts[2])
                t = con.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
                if not t:
                    return self._json({"error": "no such txn"}, 404)
                cat = b.get("category", t["category"])
                ckind = con.execute("SELECT kind FROM categories WHERE name=?", (cat,)).fetchone()
                ckind = ckind["kind"] if ckind else "expense"
                kind = b.get("kind") or (
                    ("reimbursement" if t["counterparty"] else "refund") if ckind == "expense" and t["amount"] > 0
                    else "expense" if ckind == "income" and t["amount"] < 0 else ckind)
                con.execute(
                    "UPDATE transactions SET category=?, kind=?, note=?, merchant=?, cat_source='manual', reviewed=1, updated_at=datetime('now') WHERE id=?",
                    (cat, kind, b.get("note", t["note"]), b.get("merchant") or t["merchant"], tid))
                if t["link_id"] and kind != "transfer":
                    con.execute("UPDATE transactions SET link_id=NULL WHERE id IN (?,?)", (tid, t["link_id"]))
                changed = 0
                if b.get("apply_all"):
                    field, val = ("counterparty", t["counterparty"]) if t["counterparty"] else ("merchant", t["merchant"])
                    con.execute("DELETE FROM rules WHERE field=? AND upper(pattern)=upper(?) AND is_regex=0 AND direction=?",
                                (field, val, "in" if t["amount"] > 0 else "out"))
                    con.execute(
                        "INSERT INTO rules(field, pattern, is_regex, direction, category, kind, rename, origin) VALUES (?,?,0,?,?,?,?,'user')",
                        (field, val, "in" if t["amount"] > 0 else "out", cat, b.get("kind"), b.get("merchant") if b.get("merchant") != t["merchant"] else None))
                    con.commit()
                    changed = recategorize_all(con, cfg)
                    con.execute("UPDATE transactions SET reviewed=1 WHERE " + field + "=?", (val,))
                if kind == "transfer":
                    pair_transfers(con)
                con.commit()
                self._bump()
                return self._json({"ok": True, "recategorized": changed})
            if path == "/api/balance":
                from .balances import set_balance
                set_balance(con, b["account_id"], float(b["balance"]), b.get("date"))
                from .db import log_activity
                log_activity(con, "balance", f"Balance set for {b['account_id']}: ${float(b['balance']):,.2f} — forecast and goals now use real cash")
                self._bump()
                return self._json({"ok": True})
            if path == "/api/goal":
                if b.get("id"):
                    con.execute("UPDATE goals SET target=COALESCE(?,target), auto=CASE WHEN ? IS NOT NULL THEN NULL ELSE auto END, "
                                "priority=COALESCE(?,priority), due=COALESCE(?,due), active=COALESCE(?,active) WHERE id=?",
                                (b.get("target"), b.get("target"), b.get("priority"), b.get("due"), b.get("active"), b["id"]))
                else:
                    con.execute("INSERT OR IGNORE INTO goals(name, target, due, priority) VALUES (?,?,?,?)",
                                (b["name"], float(b.get("target") or 0), b.get("due"), int(b.get("priority") or 5)))
                con.commit()
                self._bump()
                return self._json({"ok": True})
            if path == "/api/unparsed/dismiss":
                con.execute("UPDATE unparsed SET resolved=1 WHERE msg_id=?", (b["msg_id"],))
                con.commit()
                return self._json({"ok": True})
            if path == "/api/ask":
                from .assistant import answer
                return self._json({"answer": answer(con, cfg, b.get("q", ""), LOCK)})
            if path == "/api/review/ok":
                ids = b.get("ids") or []
                if ids:
                    con.executemany("UPDATE transactions SET reviewed=1 WHERE id=?", [(i,) for i in ids])
                else:
                    con.execute("UPDATE transactions SET reviewed=1")
                con.commit()
                return self._json({"ok": True})
            if path == "/api/budget":
                con.execute("UPDATE categories SET budget=? WHERE name=?", (b.get("budget") or None, b["category"]))
                con.commit()
                return self._json({"ok": True})
            if path == "/api/manual":
                from .categorize import Categorizer
                from .store import insert
                acct = b.get("account_id") or "cash"
                con.execute("INSERT OR IGNORE INTO accounts(id, name, institution, type) VALUES (?,?,?,?)", (acct, "Cash", "Cash", "cash"))
                tx = dict(account_id=acct, date=b["date"], amount=float(b["amount"]), description=b.get("description", ""), source="manual")
                tid = insert(con, Categorizer(con, cfg), tx, f"manual:{time.time()}")
                if b.get("category"):
                    con.execute("UPDATE transactions SET category=?, cat_source='manual', reviewed=1 WHERE id=?", (b["category"], tid))
                con.commit()
                return self._json({"id": tid})
            return self._json({"error": "not found"}, 404)

    return H


def serve(con, cfg, host="127.0.0.1", port=8765, sync_every=None, log=print):
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("LEDGER_DASHBOARD_PASSWORD"):
        raise SystemExit("Refusing to listen on the network without a dashboard password. "
                         "Use Tailscale Serve instead (recommended, see README) or set one with `python -m ledger setup`.")
    if sync_every:
        start_background(con, cfg, sync_every, log)
    httpd = ThreadingHTTPServer((host, port), make_handler(con, cfg))
    log(f"Dashboard → http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}")
    httpd.serve_forever()


def start_background(con, cfg, sync_every, log=print):
    """The 'alive' part: instant email via IMAP IDLE, a fallback full sync, a quick folder/notification tick,
    nightly backups, and the Telegram bot. Every thread survives errors and keeps going."""
    from .assistant import telegram_loop
    kick = threading.Event()

    def sync_once(reason):
        try:
            with LOCK:
                s = run_sync(con, cfg, log)
                new = (s.get("email") or {}).get("new", 0)
                if new or reason != "timer":
                    log(f"sync ({reason}): {json.dumps(s)}")
        except Exception:
            log("sync failed:\n" + traceback.format_exc())

    def main_loop():
        last_full = 0.0
        while True:
            fired = kick.wait(timeout=60)
            if fired:
                kick.clear()
                time.sleep(4)  # let a burst of emails land, then sync once
                kick.clear()
                sync_once("new mail")
                last_full = time.time()
            elif time.time() - last_full >= sync_every * 60:
                sync_once("timer")
                last_full = time.time()
            else:  # every minute: Downloads folder + due notifications (cheap, no network except sending)
                try:
                    with LOCK:
                        from .categorize import Categorizer
                        from .ingest_files import watch_folders
                        from .notify import detect, flush
                        watch_folders(con, cfg, Categorizer(con, cfg), log)
                        detect(con, cfg)
                        flush(con, cfg, log)
                        from .backup import nightly
                        nightly(con, cfg, log)
                except Exception:
                    log("tick failed:\n" + traceback.format_exc())

    threading.Thread(target=main_loop, name="ledger-main", daemon=True).start()
    if cfg["sync"].get("realtime", True):
        from .ingest_email import idle_forever, inboxes
        for ib in inboxes(cfg):
            pw = os.environ.get(ib["password_env"], "").replace(" ", "")
            if pw:
                threading.Thread(target=idle_forever, args=(ib, pw, kick.set, log), name=f"idle-{ib['address']}", daemon=True).start()
                log(f"Listening for new mail in real time: {ib['address']}")
    threading.Thread(target=telegram_loop, args=(con, cfg, LOCK, log), name="telegram", daemon=True).start()
