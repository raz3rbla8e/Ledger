"""Pull bank / Interac alert emails from Gmail over IMAP (app password, read-only usage) and parse them.

Add a new bank: write a parser function and append it to PARSERS. Each gets (sender, subject, body_text, email_date)
and returns a list of dicts or [] if it doesn't recognise the email.
"""
import email
import email.policy
import html
import imaplib
import os
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from . import db as dbm
from .normalize import clean_text, institution_from_text, slug
from .store import insert

SENDERS = ["tangerine.ca", "payments.interac.ca", "rbc.com", "rbcroyalbank.com", "nbc.ca", "bnc.ca", "wealthsimple.com"]

MONEY = r"\$\s?([\d,]+\.\d{2})"


def _amt(s):
    return float(s.replace(",", ""))


def _date(s, fallback):
    s = clean_text(s)
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return fallback


# ───────────────────────── parsers ─────────────────────────

def p_tangerine_cc_purchase(sender, subject, body, d):
    if "tangerine" not in sender or "credit card transaction" not in subject.lower():
        return []
    m = re.search(r"transaction of " + MONEY + r" was made on your (.+?)\s+\d{4}\s*x+\s*x+\s*x+\s*(\d{4}) at (.+?) on (\w+ \d{1,2}, \d{4})", body, re.S)
    if not m:
        return []
    amount, card, last4, merchant, when = m.groups()
    is_refund = re.search(r"refund|credit of", body, re.I)
    return [dict(account=("Tangerine", "credit", last4, card.strip()), date=_date(when, d),
                 amount=_amt(amount) if is_refund else -_amt(amount), description=clean_text(merchant))]


def p_tangerine_cc_payment(sender, subject, body, d):
    if "tangerine" not in sender or "payment has been received" not in subject.lower():
        return []
    m = re.search(r"payment of " + MONEY, body)
    if not m:
        return []
    return [dict(account=("Tangerine", "credit", None, "Tangerine Credit Card"), date=d, amount=_amt(m.group(1)),
                 description="CREDIT CARD PAYMENT RECEIVED")]


def p_tangerine_rewards(sender, subject, body, d):
    if "tangerine" not in sender or "rewards have been deposited" not in subject.lower():
        return []
    m = re.search(r"Rewards of " + MONEY + r" into your (.+?)\.", body)
    if not m:
        return []
    return [dict(account=("Tangerine", "credit", None, "Tangerine Credit Card"), date=d, amount=_amt(m.group(1)),
                 description="MONEY-BACK REWARDS")]


DATE_RX = r"Date:[\s|]*(\w{3,9}\.? \d{1,2}, \d{4})"
REF_RX = r"Reference Number:[\s|]*([A-Za-z0-9]{5,})"
MEMO_RX = r"Message:[\s|]*([^|\n][^|\n]{0,200})"


def _behalf(body):
    """'... on behalf of Jordan Example at Wealthsimple.' → ('JORDAN EXAMPLE', 'Wealthsimple'); '... on behalf of Tangerine Bank' → (None, 'Tangerine')"""
    hits = re.findall(r"on behalf of ([^|\n]+?)\s*(?:\.|\||$)", body, re.M)
    if not hits:
        return None, institution_from_text(body[-600:])
    h = hits[-1].strip()
    if " at " in h:
        who, bank = h.rsplit(" at ", 1)
        return who.strip().upper(), institution_from_text(bank) or bank.strip()
    return None, institution_from_text(h) or h


def _common(body, d):
    when = re.search(DATE_RX, body)
    ref = re.search(REF_RX, body)
    memo = re.search(MEMO_RX, body)
    return (_date(when.group(1).replace(".", ""), d) if when else d, ref.group(1) if ref else None,
            clean_text(memo.group(1)) if memo else None)


def p_interac_received(sender, subject, body, d):
    if "interac" not in sender:
        return []
    m = re.search(r"received " + MONEY + r" from (.+?) and it has been (?:automatically )?deposited", subject, re.I)
    if not m:
        return []
    amount, who = m.groups()
    inst = re.search(r"deposited into your account at (.+?)\.", body)
    inst = institution_from_text(inst.group(1)) if inst else None
    last4 = re.search(r"Account ending in (\d{3,4})", body)
    when, ref, memo = _common(body, d)
    _, from_bank = _behalf(body)
    return [dict(account=(inst or "Unknown Bank", "chequing", last4.group(1) if last4 else None, None),
                 date=when, amount=_amt(amount), description=f"INTERAC E-TRANSFER FROM {who.upper()}" + (f" ({from_bank})" if from_bank else ""),
                 counterparty=who.strip().upper(), memo=memo, ext_ref=ref, from_institution=from_bank)]


def p_interac_sent(sender, subject, body, d):
    if "interac" not in sender:
        return []
    m = re.search(r"Your " + MONEY + r" transfer to (.+?) has been (?:successfully )?(?:deposited|accepted)", subject, re.I) or \
        re.search(r"request for " + MONEY + r" transfer to (.+?) was (?:successfully )?deposited", subject, re.I)  # you paid a money request
    if m:
        amount, who = m.groups()
    else:
        m = re.search(r"(.+?) has accepted your transfer of " + MONEY, subject, re.I)
        if not m:
            return []
        who, amount = m.groups()
        who = re.sub(r"^Interac e-Transfer:\s*", "", who, flags=re.I)
    _, inst = _behalf(body)
    inst = inst or institution_from_text(sender)  # "Tangerine Bank <catch@payments.interac.ca>"
    when, ref, memo = _common(body, d)
    return [dict(account=(inst or "Unknown Bank", "chequing", None, None), date=when, amount=-_amt(amount),
                 description=f"INTERAC E-TRANSFER TO {who.upper()}", counterparty=who.strip().upper(), memo=memo, ext_ref=ref)]


def p_wealthsimple(sender, subject, body, d):
    if "wealthsimple" not in sender:
        return []
    s = subject.lower()
    amt = re.search(r"Amount:[\s|]*" + MONEY, body)
    src = re.search(r"From:[\s|]*([^|\n]+?)\s+(?:Your deposit|\||$)", body)
    if amt and re.search(r"you made a transfer|your transfer is complete", s):
        # money moving between your own Wealthsimple accounts (Spending → Savings bucket, Spending → TFSA): a transfer, never spending
        to = re.search(r"\bTo:\s*(.+?)(?:\s+(?:Track the status|We've moved|We\u2019ve moved|-{5,})|\n|$)", body)
        dest = re.sub(r"\s*\(.*?\)", "", clean_text(to.group(1)) if to else "")
        dest = re.sub(r"[^\w\s&-]", "", dest).strip() or "another account"
        return [dict(account=("Wealthsimple", "chequing", None, "Wealthsimple Chequing"), date=d, amount=-_amt(amt.group(1)),
                     description=f"TRANSFER TO {dest.upper()}")]
    if "direct deposit" in s and amt:
        payer = clean_text(src.group(1)) if src else "DIRECT DEPOSIT"
        return [dict(account=("Wealthsimple", "chequing", None, "Wealthsimple Chequing"), date=d, amount=_amt(amt.group(1)),
                     description=f"DIRECT DEPOSIT FROM {payer.upper()}")]
    m = re.search(r"(?:purchase|spent|charge|payment) of " + MONEY + r".*?(?:at|to) ([^|\n.]{2,60})", body, re.I | re.S)
    if m and re.search(r"card", s + body[:400], re.I) and not re.search(r"statement|tax|document", s):
        return [dict(account=("Wealthsimple", "chequing", None, "Wealthsimple Chequing"), date=d, amount=-_amt(m.group(1)),
                     description=clean_text(m.group(2)).upper())]
    if amt and re.search(r"deposit|received|interest", s):
        return [dict(account=("Wealthsimple", "chequing", None, "Wealthsimple Chequing"), date=d, amount=_amt(amt.group(1)),
                     description=clean_text(subject).upper())]
    return []


def p_generic_purchase(sender, subject, body, d):
    """Best-effort for other banks' purchase alerts (RBC / NBC / Wealthsimple) once you switch them on."""
    inst = institution_from_text(sender)
    if not inst or inst in ("Tangerine", "Wealthsimple"):
        return []
    m = re.search(r"(?:purchase|transaction|charge|payment) of " + MONEY + r".*?(?:at|to|with) ([A-Z0-9][^\n.]{2,60}?)(?: on | was |\.|\n)", body, re.I | re.S)
    if not m or re.search(r"statement|balance|limit reached|newsletter", subject, re.I):
        return []
    last4 = re.search(r"(?:ending in|\*{2,}|x{3,}\s?)(\d{4})", body, re.I)
    kind = "credit" if re.search(r"credit card|mastercard|visa", body, re.I) else "chequing"
    return [dict(account=(inst, kind, last4.group(1) if last4 else None, None), date=d, amount=-_amt(m.group(1)),
                 description=clean_text(m.group(2)).upper())]


def p_balance_hints(sender, subject, body, d):
    """Not transactions, but free balance information: 'Orange Alert … Chequing Account ending in 4321 … balance is below $100.00'"""
    if "tangerine" in sender and "low account balance" in subject.lower():
        m = re.search(r"ending in (\d{3,4}), because the balance is below " + MONEY, body)
        if m:
            return [dict(hint=True, account=("Tangerine", "chequing", m.group(1), None), date=d, max_balance=_amt(m.group(2)))]
    return []


NOT_TXN = re.compile(r"statement|newsletter|security|password|sign.?in|verify|welcome|survey|offer|promo|tax (slip|document)|"
                     r"limit reached|low (account )?balance|contact added|updated your|rewards earned this month|scammer|protection", re.I)
# Bank marketing and reminders: never transactions, so they shouldn't show up as "couldn't read".
NOT_TXN_SENDER = re.compile(r"forwardbanking@|promotions?@|marketing@|newsletter@|offers?@|@(?:e|email|contact|m)\.|administration@", re.I)
NOT_TXN_SUBJECT = re.compile(r"(?:payment|bill) (?:will be|is) due|due soon|annual review|cash ?back on everyday|earn up to|balance transfer|"
                             r"thinking of switching|earn \d+% interest|register for autodeposit|autodeposit (?:is )?(?:set up|registered)", re.I)
LOOKS_TXN = re.compile(r"\$\s?[\d,]+\.\d{2}|transaction|purchase|deposit|transfer|payment|withdraw", re.I)


PARSERS = [p_balance_hints, p_tangerine_cc_purchase, p_tangerine_cc_payment, p_tangerine_rewards, p_interac_received, p_interac_sent, p_wealthsimple, p_generic_purchase]


def parse_email(sender, subject, body, d):
    for p in PARSERS:
        try:
            out = p(sender.lower(), subject, body, d)
        except Exception:
            out = []
        if out:
            return out
    return []


# ───────────────────────── account resolution ─────────────────────────

def resolve_account(con, cfg, inst, type_, last4, label):
    """Map (institution, type, last4) to an account id, creating it if new."""
    base = slug(inst)
    if last4:
        acct_id = f"{base}-{'cc-' if type_ == 'credit' else ''}{last4}"
        if not con.execute("SELECT 1 FROM accounts WHERE id=?", (acct_id,)).fetchone():
            # an earlier last4-less placeholder for the same inst/type gets adopted
            ph = f"{base}-{'cc' if type_ == 'credit' else 'chequing'}"
            if con.execute("SELECT 1 FROM accounts WHERE id=?", (ph,)).fetchone():
                con.execute("UPDATE transactions SET account_id=? WHERE account_id=?", (acct_id, ph))
                con.execute("DELETE FROM accounts WHERE id=?", (ph,))
        pretty = cfg["accounts"].get(acct_id, {}).get("name") or f"{inst} {'Credit Card' if type_ == 'credit' else 'Chequing'} ••{last4}"
        dbm.ensure_account(con, acct_id, pretty, inst, type_, last4)
        return acct_id
    rows = con.execute("SELECT id FROM accounts WHERE institution=? AND type=?", (inst, type_)).fetchall()
    if len(rows) >= 1:
        return rows[0]["id"]
    acct_id = f"{base}-{'cc' if type_ == 'credit' else 'chequing'}"
    dbm.ensure_account(con, acct_id, cfg["accounts"].get(acct_id, {}).get("name") or f"{inst} {'Credit Card' if type_ == 'credit' else 'Chequing'}", inst, type_, None)
    return acct_id


# ───────────────────────── IMAP ─────────────────────────

def _body_text(msg):
    plain, htm = None, None
    for part in msg.walk():
        ct = part.get_content_type()
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            payload = part.get_content()
        except Exception:
            continue
        if ct == "text/plain" and plain is None:
            plain = payload
        elif ct == "text/html" and htm is None:
            htm = payload
    if plain and len(plain.strip()) > 40:
        return plain
    if htm:
        t = re.sub(r"(?is)<(script|style).*?</\1>", " ", htm)
        t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>", "\n", t)
        t = re.sub(r"<[^>]+>", " ", t)
        t = html.unescape(t)
        return "\n".join(re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in t.splitlines() if ln.strip())
    return plain or ""


TRUSTED_DOMAINS = ("tangerine.ca", "interac.ca", "wealthsimple.com", "rbc.com", "rbcroyalbank.com", "nbc.ca", "bnc.ca")


def _org(domain):
    parts = (domain or "").lower().strip(".>").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


def sender_authenticated(msg):
    """True if Gmail verified this really came from the bank's domain (DMARC/DKIM aligned with From).
    Anyone can send an email that *looks* like a Tangerine alert — this is what stops fake transactions."""
    from email.utils import parseaddr
    addr = parseaddr(str(msg.get("From", "")))[1].lower()
    dom = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    if not dom:
        return False, "no sender"
    results = [str(h) for h in (msg.get_all("Authentication-Results") or [])]
    gmail = [r for r in results if r.strip().lower().startswith("mx.google.com")]
    if not gmail:
        return False, "no Gmail authentication results"
    r = gmail[0].lower()  # topmost = added by Gmail on arrival; anything below could be forged by the sender
    if re.search(r"dmarc=pass[^;]*header\.from=" + re.escape(dom), r) or (re.search(r"dmarc=pass", r) and f"header.from={dom}" in r):
        return True, "dmarc"
    for m in re.finditer(r"dkim=pass[^;]*?header\.(?:i|d)=@?([a-z0-9.\-]+)", r):
        if _org(m.group(1)) == _org(dom):
            return True, "dkim"
    m = re.search(r"spf=pass[^;]*smtp\.mailfrom=(?:[^@\s;]*@)?([a-z0-9.\-]+)", r)
    if m and _org(m.group(1)) == _org(dom) and "dkim=fail" not in r:
        return True, "spf"
    return False, "failed DKIM/SPF/DMARC"


def ingest_message(con, cfg, cz, raw_bytes, stats, use_ai=True, verify=None):
    from .ai import parse_email_ai
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    mid = (msg.get("Message-ID") or "").strip() or f"nomid-{hash(raw_bytes)}"
    if con.execute("SELECT 1 FROM seen_emails WHERE msg_id=?", (mid,)).fetchone():
        return
    sender = str(msg.get("From", ""))
    subject = clean_text(str(msg.get("Subject", "")))
    try:
        d = parsedate_to_datetime(msg.get("Date")).astimezone().date().isoformat()
    except Exception:
        d = datetime.now().date().isoformat()
    body = _body_text(msg)
    if verify is None:
        verify = cfg["email"].get("require_authenticated_senders", True)
    if verify:
        from email.utils import parseaddr
        dom = parseaddr(sender)[1].lower().rsplit("@", 1)[-1]
        ok, why = sender_authenticated(msg)
        if any(dom == t or dom.endswith("." + t) for t in TRUSTED_DOMAINS) and not ok:
            con.execute("INSERT OR IGNORE INTO unparsed(msg_id, sender, subject, date, snippet, reason) VALUES (?,?,?,?,?,?)",
                        (mid, sender[:120], subject[:200], d, clean_text(body)[:300], f"QUARANTINED — {why}: possible fake bank email"))
            con.execute("INSERT OR IGNORE INTO seen_emails(msg_id, parsed) VALUES (?, -1)", (mid,))
            from .db import log_activity
            log_activity(con, "security", f"Ignored a bank-looking email that failed authenticity checks: “{subject[:80]}”", f"spoof:{mid}")
            stats["quarantined"] = stats.get("quarantined", 0) + 1
            return
    parsed = parse_email(sender, subject, body, d)
    via_ai = False
    if not parsed and LOOKS_TXN.search(subject + " " + body[:600]) and not NOT_TXN.search(subject) \
            and not NOT_TXN_SENDER.search(sender) and not NOT_TXN_SUBJECT.search(subject):
        ai = parse_email_ai(cfg, sender, subject, body, d) if use_ai else None
        if ai:
            via_ai = True
            inst = institution_from_text(ai[0].get("bank") or "") or institution_from_text(sender) or "Unknown Bank"
            parsed = [dict(account=(inst, "credit" if "credit" in (body[:800].lower()) and ai[0]["kind"] == "purchase" else "chequing", ai[0].get("last4"), None),
                           date=_date(ai[0]["date"], d) if ai[0]["date"] and not re.match(r"\d{4}-\d\d-\d\d$", ai[0]["date"]) else (ai[0]["date"] or d),
                           amount=ai[0]["amount"], description=ai[0]["description"])]
        elif ai is None:
            con.execute("INSERT OR IGNORE INTO unparsed(msg_id, sender, subject, date, snippet, reason, raw) VALUES (?,?,?,?,?,?,?)",
                        (mid, sender[:120], subject[:200], d, clean_text(body)[:300], "no parser matched; local AI not running", raw_bytes))
            stats["unparsed"] = stats.get("unparsed", 0) + 1
            return  # kept (with the original) and retried automatically once Ollama is running or a parser is added
    for i, p in enumerate(parsed):
        inst, type_, last4, label = p.pop("account")
        acct = resolve_account(con, cfg, inst, type_, last4, label)
        if p.pop("hint", False):
            con.execute("INSERT OR IGNORE INTO balance_hints(account_id, date, max_balance, source) VALUES (?,?,?,?)",
                        (acct, p["date"], p["max_balance"], "email"))
            stats["hints"] = stats.get("hints", 0) + 1
            continue
        ext = p.pop("ext_ref", None)
        tx = dict(p, account_id=acct, source="email", ext_ref=ext)
        if via_ai:
            tx["_needs_review"] = True
            tx["confidence"] = 0.6
        tid = insert(con, cz, tx, f"email:{mid}:{i}", type_, stats)
        if via_ai and tid:
            con.execute("UPDATE transactions SET reviewed=0, confidence=0.6 WHERE id=?", (tid,))
        if ext:
            add = con.execute("SELECT txn_id FROM refs WHERE ref=?", (f"email:{mid}:{i}",)).fetchone()
            if add:
                con.execute("INSERT OR IGNORE INTO refs(ref, txn_id) VALUES (?,?)", (f"interac:{ext}:{'in' if tx['amount'] > 0 else 'out'}", add["txn_id"]))
    con.execute("INSERT OR IGNORE INTO seen_emails(msg_id, parsed) VALUES (?,?)", (mid, len(parsed)))
    con.execute("UPDATE unparsed SET resolved=1, raw=NULL WHERE msg_id=?", (mid,))
    stats["emails"] = stats.get("emails", 0) + 1


def inboxes(cfg):
    ec = cfg["email"]
    lst = list(ec.get("inboxes") or [])
    if not lst and ec.get("address"):
        lst = [dict(address=ec["address"], password_env="LEDGER_IMAP_PASSWORD")]
    for ib in lst:
        ib.setdefault("imap_host", ec.get("imap_host", "imap.gmail.com"))
        ib.setdefault("password_env", "LEDGER_IMAP_PASSWORD")
    return lst


def sync_email(con, cfg, cz, log=print):
    ec = cfg["email"]
    stats = {}
    if not ec.get("enabled", True):
        return stats
    for ib in inboxes(cfg):
        pw = os.environ.get(ib["password_env"], "").replace(" ", "")
        if not pw:
            log(f"  email {ib['address']}: no {ib['password_env']} in .env — skipping")
            from .db import source_fail
            source_fail(con, f"inbox:{ib['address']}", "email", f"No app password set ({ib['password_env']} in .env)")
            continue
        from .db import source_fail, source_ok
        key = f"inbox:{ib['address']}"
        before = stats.get("new", 0)
        try:
            _sync_inbox(con, cfg, cz, ib, pw, stats, log)
            last = con.execute("SELECT max(seen_at) FROM seen_emails").fetchone()[0]
            source_ok(con, key, "email", stats.get("new", 0) - before, last)
        except (imaplib.IMAP4.error, OSError) as e:
            log(f"  email {ib['address']}: login/IMAP error — {e}")
            source_fail(con, key, "email", e)
            stats["errors"] = stats.get("errors", 0) + 1
        con.commit()
    return stats


def _open(ib, pw):
    M = imaplib.IMAP4_SSL(ib["imap_host"], timeout=60)
    M.login(ib["address"], pw)
    gmail = "gmail" in ib["imap_host"]
    typ, data = M.select('"[Gmail]/All Mail"' if gmail else "INBOX", readonly=True)
    if typ != "OK":
        M.select("INBOX", readonly=True)
    return M, gmail


def _uidvalidity(M):
    typ, data = M.response("UIDVALIDITY")
    try:
        return int(data[0])
    except Exception:
        return 0


def _message_ids(M, uids):
    """{uid: '<message-id>'} for a batch of UIDs in a single FETCH."""
    typ, data = M.uid("FETCH", ",".join(map(str, uids)), "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
    out = {}
    if typ != "OK" or not data:
        return out
    for i, part in enumerate(data):
        if isinstance(part, tuple) and len(part) >= 2:
            u = re.search(rb"UID (\d+)", part[0])
            if not u and i + 1 < len(data) and isinstance(data[i + 1], bytes):  # servers may put UID after the literal
                u = re.search(rb"UID (\d+)", data[i + 1])
            m = re.search(rb"<[^>]+>", part[1] or b"")
            if u and m:
                out[int(u.group(1))] = m.group(0).decode(errors="ignore")
    return out


def _sync_inbox(con, cfg, cz, ib, pw, stats, log):
    """Only asks Gmail for messages newer than the last one seen (UID tracking), and only downloads a full
    email after checking its Message-ID isn't already stored. Read-only: nothing is marked as read."""
    key = f"email_last_sync:{ib['address']}"
    ukey = f"email_uid:{ib['address']}"
    last = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    since = (datetime.fromisoformat(last["value"]) - timedelta(days=3)).date() if last else \
        datetime.fromisoformat(cfg["email"].get("backfill_since", "2025-09-01")).date()
    M, gmail = _open(ib, pw)
    try:
        uv = _uidvalidity(M)
        st = con.execute("SELECT value FROM meta WHERE key=?", (ukey,)).fetchone()
        last_uid = 0
        if st:
            try:
                sv, su = st["value"].split(":")
                last_uid = int(su) if int(sv) == uv else 0
            except ValueError:
                last_uid = 0
        rng = ["UID", f"{last_uid + 1}:*"] if last_uid else []
        uids = []
        if gmail:
            q = f'from:({" OR ".join(SENDERS)}) after:{since:%Y/%m/%d}'
            typ, data = M.uid("SEARCH", *rng, "X-GM-RAW", f'"{q}"')
            uids = data[0].split() if typ == "OK" and data and data[0] else []
        else:
            for snd in SENDERS:
                typ, data = M.uid("SEARCH", *rng, f'(FROM "{snd}" SINCE {since:%d-%b-%Y})')
                if typ == "OK" and data and data[0]:
                    uids += data[0].split()
        uids = sorted({int(u) for u in uids if int(u) > last_uid})
        if len(uids) > 50:
            log(f"  email {ib['address']}: checking {len(uids)} bank emails…")
        new = 0
        for i in range(0, len(uids), 200):  # Message-IDs for 200 emails per round trip, then bodies only for unseen ones
            chunk = uids[i:i + 200]
            mids = _message_ids(M, chunk)
            for uid in chunk:
                mid = mids.get(uid, "")
                if mid and con.execute("SELECT 1 FROM seen_emails WHERE msg_id=?", (mid,)).fetchone():
                    continue
                typ, data = M.uid("FETCH", str(uid), "(BODY.PEEK[])")
                if typ != "OK" or not data or not isinstance(data[0], tuple):
                    continue
                ingest_message(con, cfg, cz, data[0][1], stats)
                new += 1
            # save progress as we go, so a restart mid-backfill resumes instead of starting over
            con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (ukey, f"{uv}:{chunk[-1]}"))
            con.commit()
            if len(uids) > 200:
                log(f"  email {ib['address']}: {min(i + 200, len(uids))}/{len(uids)} checked, {new} new so far")
        if new:
            log(f"  email {ib['address']}: {new} new")
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, datetime.now().isoformat()))
        con.commit()
    finally:
        try:
            M.logout()
        except Exception:
            pass


# ───────────────────────── real-time: IMAP IDLE ─────────────────────────

IDLE_SECONDS = 20 * 60  # re-issue IDLE this often (Gmail drops idle connections after ~30 min)


def idle_forever(ib, pw, on_new_mail, log=print, stop=None):
    """Hold an IMAP IDLE connection: Gmail pushes '* n EXISTS' the moment mail arrives → on_new_mail().
    After IDLE_SECONDS of silence it starts a fresh connection: once a read on an SSL socket has timed out,
    Python won't read from it again ("cannot read from timed out object"), so the socket is never reused."""
    import socket
    import time
    backoff = 5
    while not (stop and stop.is_set()):
        M = None
        try:
            M, _ = _open(ib, pw)
            backoff = 5
            while not (stop and stop.is_set()):
                tag = M._new_tag()
                M.send(tag + b" IDLE\r\n")
                if not M.readline().startswith(b"+"):
                    raise imaplib.IMAP4.error("server refused IDLE")
                M.sock.settimeout(IDLE_SECONDS)
                got = False
                try:
                    while True:
                        line = M.readline()
                        if not line:
                            raise ConnectionError("connection closed")
                        if b"EXISTS" in line:
                            got = True
                            break
                except (socket.timeout, TimeoutError):
                    break  # quiet period over → reconnect (normal, not logged)
                M.sock.settimeout(60)
                M.send(b"DONE\r\n")
                while True:  # drain until our tagged completion
                    ln = M.readline()
                    if not ln or ln.startswith(tag):
                        break
                if got:
                    on_new_mail()
        except Exception as e:
            log(f"  idle {ib['address']}: {e} — reconnecting in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
        finally:
            try:
                if M:
                    M.shutdown()
            except Exception:
                pass


def retry_unparsed(con, cfg, cz, stats):
    """Emails nothing could read earlier get another go (new parser, or local AI now running)."""
    from .ai import provider
    rows = con.execute("SELECT msg_id, raw FROM unparsed WHERE resolved=0 AND raw IS NOT NULL AND reason NOT LIKE 'QUARANTINED%'").fetchall()
    if not rows:
        return 0
    use_ai = provider(cfg) != "none"
    n = 0
    for r in rows:
        before = con.execute("SELECT resolved FROM unparsed WHERE msg_id=?", (r["msg_id"],)).fetchone()[0]
        ingest_message(con, cfg, cz, r["raw"], stats, use_ai=use_ai)
        if con.execute("SELECT resolved FROM unparsed WHERE msg_id=?", (r["msg_id"],)).fetchone()[0] and not before:
            n += 1
    con.commit()
    return n
