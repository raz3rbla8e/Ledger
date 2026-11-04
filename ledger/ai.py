"""AI helpers — privacy-first.
provider "auto" uses a LOCAL Ollama model when it's running (nothing leaves your PC) and is off otherwise.
Claude is only used when you explicitly choose "anthropic"."""
import json
import os
import re
import urllib.request

_probe = {}


def provider(cfg):
    p = cfg["ai"].get("provider", "auto")
    if p == "auto":
        url = cfg["ai"].get("ollama_url", "http://localhost:11434")
        if url not in _probe:
            try:
                urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=2).read()
                _probe[url] = True
            except Exception:
                _probe[url] = False
        return "ollama" if _probe[url] else "none"
    if p == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        return "none"
    return p


def _anthropic(cfg, prompt, max_tokens=1500, system=None):
    body = {"model": cfg["ai"].get("anthropic_model", "claude-haiku-5-5"), "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return "".join(b.get("text", "") for b in json.load(r).get("content", []))


def _ollama(cfg, prompt, json_mode=True, system=None, schema=None):
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = {"model": cfg["ai"].get("ollama_model", "qwen3:8b"), "stream": False, "messages": msgs, "options": {"temperature": 0},
            "think": False}
    if schema:
        body["format"] = schema  # constrained decoding: the model physically can't return anything but this shape
    elif json_mode:
        body["format"] = "json"
    req = urllib.request.Request(cfg["ai"].get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/chat",
                                 data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)["message"]["content"]


def complete(cfg, prompt, json_mode=True, system=None, which=None, schema=None):
    which = which or provider(cfg)
    if which == "anthropic":
        return _anthropic(cfg, prompt, system=system)
    if which == "ollama":
        return _ollama(cfg, prompt, json_mode, system, schema)
    raise RuntimeError("no AI provider available")


def _json(txt):
    txt = txt[txt.find("{"): txt.rfind("}") + 1]
    return json.loads(txt)


# ───────────────── unknown merchants → category ─────────────────

def ai_fill(con, cfg, log=print):
    if provider(cfg) == "none":
        return 0
    rows = con.execute(
        """SELECT merchant, CASE WHEN amount>0 THEN 'in' ELSE 'out' END d, count(*) n FROM transactions
           WHERE cat_source='none' AND counterparty IS NULL AND merchant IS NOT NULL AND merchant!=''
           AND NOT EXISTS (SELECT 1 FROM rules r WHERE upper(r.pattern)=upper(merchant) AND r.origin='ai')
           GROUP BY merchant, d ORDER BY n DESC LIMIT 40""").fetchall()
    if not rows:
        return 0
    cats = [r["name"] for r in con.execute("SELECT name FROM categories ORDER BY sort")]
    lines = "\n".join(f"- {r['merchant']} ({'money in' if r['d'] == 'in' else 'money out'})" for r in rows)
    prompt = ("Categorize Canadian personal-finance merchants (Ottawa, ON). Allowed categories: " + ", ".join(cats) +
              ". Use \"Uncategorized\" if unsure. Return {\"items\": [{\"merchant\": <exactly as written>, \"category\": <one allowed>}]}.\n\n" + lines)
    schema = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
        "merchant": {"type": "string"}, "category": {"type": "string", "enum": cats}}, "required": ["merchant", "category"]}}},
        "required": ["items"]}
    try:
        raw = _json(complete(cfg, prompt, schema=schema))
        answers = {i.get("merchant"): i.get("category") for i in raw.get("items", [])} if "items" in raw else raw
    except Exception as e:
        log(f"  ai: skipped ({e})")
        return 0
    from .db import log_activity
    n = 0
    for r in rows:
        cat = answers.get(r["merchant"])
        if cat in cats and cat != "Uncategorized":
            con.execute("INSERT INTO rules(field, pattern, is_regex, direction, category, origin) VALUES ('merchant', ?, 0, ?, ?, 'ai')",
                        (r["merchant"], r["d"], cat))
            n += 1
    if n:
        log_activity(con, "learn", f"Figured out {n} new merchant{'s' if n > 1 else ''} on its own ({provider(cfg)})")
    con.commit()
    return n


# ───────────────── unknown bank emails → transaction ─────────────────

EMAIL_PROMPT = """You extract a single bank transaction from a Canadian bank / Interac notification email.
Return ONLY JSON: {{"is_transaction": bool, "amount": number (positive), "direction": "in"|"out",
"counterparty_or_merchant": str, "date": "YYYY-MM-DD" or null, "bank": str or null, "account_last4": str or null,
"kind": "purchase"|"etransfer"|"deposit"|"payment"|"other"}}.
Marketing, statements-ready, low-balance and security emails are NOT transactions.

From: {sender}
Subject: {subject}
Received: {date}
Body:
{body}"""


EMAIL_SCHEMA = {"type": "object", "properties": {
    "is_transaction": {"type": "boolean"}, "amount": {"type": "number"}, "direction": {"type": "string", "enum": ["in", "out"]},
    "counterparty_or_merchant": {"type": "string"}, "date": {"type": ["string", "null"]}, "bank": {"type": ["string", "null"]},
    "account_last4": {"type": ["string", "null"]}, "kind": {"type": "string", "enum": ["purchase", "etransfer", "deposit", "payment", "other"]}},
    "required": ["is_transaction", "amount", "direction", "counterparty_or_merchant", "kind"]}


def parse_email_ai(cfg, sender, subject, body, d):
    if provider(cfg) == "none":
        return None
    body = re.sub(r"https?://\S+", "", body)
    body = re.sub(r"\s{3,}", "\n", body)[:3500]
    try:
        out = _json(complete(cfg, EMAIL_PROMPT.format(sender=sender, subject=subject, date=d, body=body), schema=EMAIL_SCHEMA))
    except Exception:
        return None
    if not out.get("is_transaction") or not out.get("amount"):
        return []
    try:
        amt = abs(float(str(out["amount"]).replace(",", "").replace("$", "")))
    except ValueError:
        return None
    return [dict(amount=amt if out.get("direction") == "in" else -amt, description=str(out.get("counterparty_or_merchant") or subject).upper()[:80],
                 date=out.get("date") or d, bank=out.get("bank"), last4=out.get("account_last4"), kind=out.get("kind"))]
