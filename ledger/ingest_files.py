"""Statement-file importer. Drop exports into  ~/Ledger/inbox/<account-id>/  (folder name = account).

Handles CSV from RBC, Tangerine, National Bank, Wealthsimple and most others (columns are auto-detected),
plus OFX / QFX ("Quicken" / "Money" downloads). Imported files are moved to inbox/_imported/.
"""
import csv
import hashlib
import io
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import db as dbm
from .normalize import counterparty_from_description, institution_from_text
from .store import insert

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%m-%d-%Y", "%d-%m-%Y", "%b %d, %Y", "%d %b %Y", "%Y%m%d", "%m/%d/%y", "%B %d, %Y"]


def _parse_date(s, prefer_dmy=False):
    s = (s or "").strip().split("T")[0].split(" ")[0] if re.match(r"\d{4}-\d{2}-\d{2}", (s or "").strip()) else (s or "").strip()
    fmts = DATE_FORMATS[:]
    if prefer_dmy:
        fmts.remove("%d/%m/%Y")
        fmts.insert(1, "%d/%m/%Y")
    for f in fmts:
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return None


def _num(s):
    s = (s or "").strip().replace("$", "").replace(",", "").replace(" ", "").replace(" ", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _find(headers, *cands):
    low = [h.lower().strip() for h in headers]
    for c in cands:
        for i, h in enumerate(low):
            if h == c:
                return i
    for c in cands:
        for i, h in enumerate(low):
            if c in h:
                return i
    return None


def parse_csv(text):
    """Returns list of (date, amount, description). Sign convention: + in, - out (as the bank exported it)."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    # find header row (some banks prepend account info lines)
    hdr_i = 0
    for i, r in enumerate(rows[:15]):
        joined = " ".join(r).lower()
        if ("date" in joined or "transaction" in joined) and any(k in joined for k in ("amount", "debit", "credit", "cad", "withdraw", "deposit", "montant")):
            hdr_i = i
            break
    hdr = rows[hdr_i]
    body = rows[hdr_i + 1:]
    has_header = any(re.search(r"[a-z]", h, re.I) for h in hdr) and not _parse_date(hdr[0])
    if not has_header:  # headerless (e.g. old RBC / TD): date, desc, debit, credit, [balance]
        body = rows
        hdr = ["date", "description", "debit", "credit", "balance"][: len(rows[0])]
    i_date = _find(hdr, "transaction date", "date", "posted date", "posting date", "trans. date", "date de transaction")
    i_amt = _find(hdr, "amount", "cad$", "cad", "montant", "net amount")
    i_debit = _find(hdr, "debit", "withdrawal", "withdrawals", "money out", "débit", "retrait")
    i_credit = _find(hdr, "credit", "deposit", "deposits", "money in", "crédit", "dépôt")
    i_bal = _find(hdr, "balance", "total balance", "solde")
    used = (i_date, i_amt, i_debit, i_credit, i_bal)
    desc_idx = [i for i, h in enumerate(hdr) if re.search(r"desc|name|payee|merchant|details|libell", h, re.I) and i not in used]
    if not desc_idx:
        desc_idx = [i for i, h in enumerate(hdr) if re.search(r"memo|transaction$", h, re.I) and i not in used]
    if i_date is None:
        i_date = 0
    # detect d/m vs m/d
    dmy = any(re.match(r"\s*(1[3-9]|2\d|3[01])[/-]\d{1,2}[/-]\d{2,4}", r[i_date]) for r in body if len(r) > i_date)
    out = []
    for r in body:
        if len(r) <= i_date:
            continue
        d = _parse_date(r[i_date], dmy)
        if not d:
            continue
        if i_amt is not None and i_amt < len(r) and _num(r[i_amt]) is not None and not (i_debit is not None and i_credit is not None and i_amt in (i_debit, i_credit)):
            amt = _num(r[i_amt])
        else:
            dr = _num(r[i_debit]) if i_debit is not None and i_debit < len(r) else None
            cr = _num(r[i_credit]) if i_credit is not None and i_credit < len(r) else None
            if dr is None and cr is None:
                continue
            amt = (cr or 0) - abs(dr or 0)
        desc = " ".join(r[i].strip() for i in desc_idx if i < len(r) and r[i].strip())
        bal = _num(r[i_bal]) if i_bal is not None and i_bal < len(r) else None
        out.append((d, amt, desc or "(no description)", bal))
    return out


def parse_ofx(text):
    out = []
    acct = re.search(r"<ACCTID>([^<\r\n]+)", text)
    for block in re.findall(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|</BANKTRANLIST>)", text, re.S | re.I):
        g = lambda tag: (re.search(rf"<{tag}>([^<\r\n]*)", block, re.I) or [None, None])[1]
        d = g("DTPOSTED") or g("DTUSER")
        amt = _num(g("TRNAMT"))
        if not d or amt is None:
            continue
        name, memo = (g("NAME") or "").strip(), (g("MEMO") or "").strip()
        desc = name if not memo or memo in name else f"{name} {memo}".strip()
        out.append((_parse_date(d[:8]), amt, desc, (g("FITID") or "").strip()))
    return out, (acct.group(1).strip() if acct else None)


def _guess_type(acct_id):
    a = acct_id.lower()
    if any(k in a for k in ("cc", "credit", "visa", "mastercard", "mc", "amex")):
        return "credit"
    if any(k in a for k in ("invest", "tfsa", "rrsp", "fhsa")):
        return "investment"
    if "saving" in a:
        return "savings"
    return "chequing"


def import_file(con, cfg, cz, path: Path, acct_id: str, stats):
    inst = institution_from_text(acct_id) or acct_id.split("-")[0].title()
    acct = con.execute("SELECT * FROM accounts WHERE id=?", (acct_id,)).fetchone() or dbm.ensure_account(
        con, acct_id, cfg["accounts"].get(acct_id, {}).get("name") or acct_id.replace("-", " ").title(), inst, _guess_type(acct_id), None)
    atype = acct["type"]
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    ext = path.suffix.lower()
    if ext in (".ofx", ".qfx", ".qbo") or "<OFX>" in text.upper()[:2000]:
        rows, _ = parse_ofx(text)
        src = "ofx"
        lb = re.search(r"<LEDGERBAL>.*?<BALAMT>([-\d.]+).*?<DTASOF>(\d{8})", text, re.S | re.I)
        if lb:
            con.execute("INSERT INTO balance_anchors(account_id, date, balance, source) VALUES (?,?,?,?)",
                        (acct_id, _parse_date(lb.group(2)), float(lb.group(1)) * (-1 if atype == "credit" else 1), "statement"))
    else:
        rows4 = parse_csv(text)
        rows = [(d, a, s_, None) for d, a, s_, b in rows4]
        src = "csv"
        # a balance column = free, exact balance anchor (latest row)
        withbal = [(d, b) for d, a, s_, b in rows4 if b is not None]
        if withbal:
            newest_first = withbal[0][0] > withbal[-1][0]
            d, b = withbal[0] if newest_first else withbal[-1]
            con.execute("INSERT INTO balance_anchors(account_id, date, balance, source) VALUES (?,?,?,?)", (acct_id, d, b, "statement"))
    if not rows:
        return 0
    invert = cfg["accounts"].get(acct_id, {}).get("invert")
    if invert is None and atype == "credit":
        # credit card exports often list purchases as positive. If most rows are positive, flip.
        pos = sum(1 for r in rows if r[1] > 0)
        invert = pos > len(rows) * 0.6
    seen = {}
    n = 0
    for d, amt, desc, fitid in rows:
        if invert:
            amt = -amt
        key = (d, round(amt, 2), desc.upper())
        seen[key] = seen.get(key, 0) + 1
        ref = f"{src}:{acct_id}:{fitid}" if fitid else "csv:" + acct_id + ":" + hashlib.sha1(f"{key}|{seen[key]}".encode()).hexdigest()[:16]
        tx = dict(account_id=acct_id, date=d, amount=amt, description=desc, source=src)
        cp = counterparty_from_description(desc)
        if cp:
            tx["counterparty"] = cp
        if insert(con, cz, tx, ref, atype, stats):
            n += 1
    return n


def sync_files(con, cfg, cz, log=print):
    inbox: Path = cfg["_inbox"]
    done = inbox / "_imported"
    stats = {}
    for f in sorted(inbox.rglob("*")):
        if not f.is_file() or "_imported" in f.parts or f.name.startswith(".") or f.suffix.lower() not in (".csv", ".ofx", ".qfx", ".qbo", ".txt"):
            continue
        rel = f.relative_to(inbox)
        if len(rel.parts) < 2:
            log(f"  files: {f.name} is loose in the inbox — put it inside a folder named after the account (e.g. inbox/rbc-chequing/)")
            continue
        acct_id = rel.parts[0].lower()
        n = import_file(con, cfg, cz, f, acct_id, stats)
        con.commit()
        dest = done / acct_id
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), dest / f"{datetime.now():%Y%m%d-%H%M%S}-{f.name}")
        log(f"  files: {rel} → {n} rows stored")
        stats["files"] = stats.get("files", 0) + 1
    return stats


# ───────────────────────── Downloads watcher ─────────────────────────

SIGNATURES = [  # (regex on first lines, institution, type)
    (r"^\ufeff?Transaction date,Transaction,Name,Memo,Amount", "Tangerine", "credit"),
    (r"^\ufeff?Date,Transaction,Name,Memo,Amount", "Tangerine", "chequing"),
    (r'^\ufeff?"?Account Type"?,"?Account Number"?,"?Transaction Date', "RBC", None),
    (r"^\ufeff?Date;Description;Category;Debit;Credit;Balance", "National Bank", "chequing"),
    (r"^\ufeff?date,transaction,description,amount,balance", "Wealthsimple", "chequing"),
]


def detect_bank_file(path: Path):
    """Return (institution, type, last4) for a bank export, or None if it isn't one."""
    try:
        head = path.read_bytes()[:4000].decode("utf-8-sig", errors="ignore")
    except OSError:
        return None
    if "<OFX>" in head.upper() or "OFXHEADER" in head.upper():
        org = re.search(r"<ORG>([^<\r\n]+)", head) or re.search(r"<FID>([^<\r\n]+)", head)
        acct = re.search(r"<ACCTID>([^<\r\n]+)", head)
        inst = institution_from_text(org.group(1) if org else path.name) or institution_from_text(path.name)
        if not inst:
            return None
        typ = "credit" if "CREDITCARDMSGSRSV1" in head.upper() else "chequing"
        return inst, typ, (re.sub(r"\D", "", acct.group(1))[-4:] if acct else None)
    for rx, inst, typ in SIGNATURES:
        if re.search(rx, head, re.I):
            last4 = None
            if inst == "RBC":
                rows = list(csv.reader(io.StringIO(head)))
                second = rows[1] if len(rows) > 1 else []
                typ = "credit" if second and re.search(r"visa|mastercard|credit", second[0], re.I) else "chequing"
                digits = re.sub(r"\D", "", second[1]) if len(second) > 1 else ""
                last4 = digits[-4:] or None
            return inst, typ, last4
    return None


def watch_folders(con, cfg, cz, log=print):
    import hashlib as _h
    from .db import log_activity
    from .ingest_email import resolve_account
    stats = {}
    for folder in cfg["files"].get("watch", []):
        p = Path(folder).expanduser()
        if not p.is_dir():
            continue
        cutoff = datetime.now().timestamp() - 45 * 86400
        for f in p.iterdir():
            if not f.is_file() or f.suffix.lower() not in (".csv", ".ofx", ".qfx", ".qbo") or f.stat().st_mtime < cutoff:
                continue
            digest = _h.sha1(f.read_bytes()).hexdigest()
            if con.execute("SELECT 1 FROM meta WHERE key=?", (f"file:{digest}",)).fetchone():
                continue
            det = detect_bank_file(f)
            con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (f"file:{digest}", f.name))
            if not det:
                continue
            inst, typ, last4 = det
            acct = resolve_account(con, cfg, inst, typ, last4, None)
            n = import_file(con, cfg, cz, f, acct, stats)
            log_activity(con, "import", f"Imported {f.name} from {p.name} → {acct} ({n} new)")
            log(f"  watch: {f.name} → {acct}: {n} new")
            stats["files"] = stats.get("files", 0) + 1
        con.commit()
    return stats
