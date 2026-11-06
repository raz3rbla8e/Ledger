"""AI helpers — privacy-first.
provider "auto" uses a LOCAL Ollama model when it's running (nothing leaves your PC) and is off otherwise.
Claude is only used when you explicitly choose "anthropic"."""
import json
import os
import re
import urllib.request

_probe = {}          # url → (checked_at, state)
_started = {"at": 0.0}
PULL = {"model": None, "status": None, "done": 0, "total": 0, "error": None, "running": False}
PROBE_TTL = 300      # re-check every 5 minutes, so starting Ollama later is noticed without restarting Ledger


def _ollama_exe():
    import shutil
    from pathlib import Path
    for p in (shutil.which("ollama"), Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe",
              Path("/Applications/Ollama.app/Contents/Resources/ollama"), Path("/usr/local/bin/ollama")):
        if p and Path(p).is_file():
            return str(p)
    return None


def ollama_state(cfg):
    """'ready' | 'no-model' (running, but the model isn't downloaded) | 'stopped' (installed, not running) | 'missing'.
    If Ollama is installed but not running, start it quietly (at most every 30 min) — it only serves this PC."""
    import time
    url = cfg["ai"].get("ollama_url", "http://localhost:11434").rstrip("/")
    hit = _probe.get(url)
    if hit and time.time() - hit[0] < PROBE_TTL:
        return hit[1]
    want = cfg["ai"].get("ollama_model", "qwen3:8b")
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=2) as r:
            names = {m.get("name", "") for m in json.load(r).get("models", [])}
        have = want in names or f"{want}:latest" in names or any(n.split(":")[0] == want for n in names)
        state = "ready" if have else "no-model"
        if not have and "localhost" in url and cfg["ai"].get("auto_pull", True):
            _pull(url, want)
            state = "pulling" if PULL["running"] else state
    except Exception:
        exe = _ollama_exe()
        state = "stopped" if exe else "missing"
        if exe and cfg["ai"].get("autostart_ollama", True) and "localhost" in url and time.time() - _started["at"] > 1800:
            _started["at"] = time.time()
            try:
                import subprocess
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
                subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            except Exception:
                pass
            hit = None  # check again soon
            _probe[url] = (time.time() - PROBE_TTL + 20, state)
            return state
    _probe[url] = (time.time(), state)
    return state


def _pull(url, model):
    """Ollama is installed and running but the model isn't there yet: download it in the background (one time, ~5 GB),
    from Ollama's own model library, through Ollama itself. Progress shows under Settings."""
    import threading, time
    if PULL["running"] or (PULL["model"] == model and PULL["error"] and time.time() - PULL.get("at", 0) < 1800):
        return

    def go():
        PULL.update(model=model, status="starting", done=0, total=0, error=None, running=True, at=time.time())
        try:
            req = urllib.request.Request(url + "/api/pull", data=json.dumps({"model": model, "stream": True}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60 * 60 * 3) as r:
                for line in r:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("error"):
                        raise RuntimeError(ev["error"])
                    PULL.update(status=ev.get("status", ""), done=ev.get("completed", PULL["done"]) or PULL["done"],
                                total=ev.get("total", PULL["total"]) or PULL["total"])
            PULL.update(status="success")
            _probe.clear()  # re-check right away: the AI is ready now
        except Exception as e:
            PULL.update(error=str(e)[:200], at=time.time())
        finally:
            PULL["running"] = False
    threading.Thread(target=go, name="ollama-pull", daemon=True).start()
    time.sleep(0.2)


def provider(cfg):
    p = cfg["ai"].get("provider", "auto")
    if p == "auto":
        return "ollama" if ollama_state(cfg) == "ready" else "none"
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
