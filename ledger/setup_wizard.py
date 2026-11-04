"""`python -m ledger setup` — one guided pass. Re-run any time; it skips what already works.

1. personal profile (config.toml) — imported from a file if you have one
2. Gmail app passwords → tested live → stored in the OS keychain (never in git, never in plain text on Windows/macOS)
3. Telegram bot (alerts + chat)
4. phone: widget token, Apple Pay token, optional dashboard password for Tailscale
5. local AI check
6. autostart
"""
import getpass
import imaplib
import json
import os
import secrets
import shutil
import time
import tomllib
import urllib.request
from pathlib import Path

from . import secrets_store
from .ingest_email import inboxes


def _test_imap(host, user, pw):
    try:
        M = imaplib.IMAP4_SSL(host, timeout=30)
        M.login(user, pw.replace(" ", ""))
        M.logout()
        return None
    except Exception as e:
        return str(e)


AUTO = False  # `setup --auto`: no questions — keep what works, accept defaults, skip anything that needs typing


def _yes(q, default=True):
    if AUTO:
        print(f"{q} → {'yes' if default else 'no'} (auto)")
        return default
    a = input(f"{q} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not a else a in ("y", "yes")


def _import_profile(cfg):
    target = cfg["_home"] / "config.toml"
    candidates = [Path.home() / "Downloads" / n for n in ("ledger-config.toml", "ledger-personal-config.toml")]
    for c in candidates:
        if c.exists():
            try:
                tomllib.loads(c.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"   ✗ {c.name} isn't valid TOML ({e}) — skipped")
                continue
            if _yes(f"   Found {c} — use it as your Ledger profile?"):
                if target.exists():
                    shutil.copy2(target, target.with_suffix(".toml.bak"))
                shutil.move(str(c), target)
                print(f"   ✓ profile installed at {target} (previous one saved as config.toml.bak)")
                return True
    return False


def run(cfg, auto=False):
    global AUTO
    AUTO = auto
    env = cfg["_home"] / ".env"
    store = secrets_store.BACKEND if secrets_store.keychain_available() else ".env file"
    print(f"\nLedger setup — your data folder is {cfg['_home']}\nSecrets are stored in: {store}\n")

    print("1) Personal profile")
    if _import_profile(cfg):
        from . import config
        cfg = config.load()
    elif not cfg["me"].get("names") and not AUTO:
        name = input("   Your full name as it appears on e-transfers (e.g. 'Jane A Doe'): ").strip()
        if name:
            print("   → add it (and any variants) under [me] names in config.toml")
    elif not cfg["me"].get("names"):
        print("   ✗ no profile yet — fill in [me] in config.toml (see config.example.toml)")
    else:
        print("   ✓ profile already set")

    print("\n2) Gmail app passwords — one per inbox at https://myaccount.google.com/apppasswords")
    print("   (needs 2-Step Verification on that Google account). Ledger only reads bank/Interac emails and never marks anything read.")
    if not inboxes(cfg):
        print("   No inboxes in config.toml yet — add an [[email.inboxes]] block (see config.example.toml) and re-run.")
    for ib in inboxes(cfg):
        cur = secrets_store.get(ib["password_env"])
        err = _test_imap(ib["imap_host"], ib["address"], cur) if cur else "no app password saved yet"
        if not err:
            print(f"   ✓ {ib['address']} works")
            continue
        if AUTO:
            print(f"   ✗ {ib['address']}: {err}\n     → run `python -m ledger setup` to enter a new app password for it")
            continue
        while True:
            pw = getpass.getpass(f"   App password for {ib['address']} (hidden, Enter to skip): ").strip()
            if not pw:
                print("     skipped")
                break
            err = _test_imap(ib["imap_host"], ib["address"], pw)
            if err:
                print(f"     ✗ didn't work: {err}")
                continue
            where = secrets_store.set(ib["password_env"], pw.replace(" ", ""), env)
            print(f"     ✓ connected — saved to {where}")
            break

    print("\n3) Telegram — alerts, morning brief, and chat. In Telegram: @BotFather → /newbot → copy the token.")
    if secrets_store.get("TELEGRAM_BOT_TOKEN") and secrets_store.get("TELEGRAM_CHAT_ID"):
        print("   ✓ already linked")
    elif AUTO:
        print("   not linked — run `python -m ledger setup` to add it")
    else:
        tok = getpass.getpass("   Bot token (hidden, Enter to skip): ").strip()
        if tok:
            secrets_store.set("TELEGRAM_BOT_TOKEN", tok, env)
            print("   Send your new bot any message from your phone… (waiting up to 2 minutes)")
            chat, deadline = None, time.time() + 120
            while time.time() < deadline and not chat:
                try:
                    with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/getUpdates?timeout=20", timeout=30) as r:
                        for u in json.load(r).get("result", []):
                            chat = str((u.get("message") or {}).get("chat", {}).get("id") or "") or chat
                except Exception as e:
                    print(f"   ✗ {e}")
                    break
            if chat:
                secrets_store.set("TELEGRAM_CHAT_ID", chat, env)
                from .notify import send_telegram
                send_telegram(cfg, "👋 Ledger is connected. I'll only ever talk to this chat. Try /today", chat)
                print("   ✓ linked — check Telegram")
            else:
                print("   didn't see a message — re-run setup later")

    print("\n4) Phone")
    if not secrets_store.get("LEDGER_WIDGET_TOKEN"):
        secrets_store.set("LEDGER_WIDGET_TOKEN", secrets.token_urlsafe(24), env)
    if not secrets_store.get("LEDGER_HOOK_TOKEN"):
        secrets_store.set("LEDGER_HOOK_TOKEN", secrets.token_urlsafe(24), env)
    if AUTO:
        print("   ✓ phone tokens ready (run `python -m ledger setup` when you set up the iPhone widget to see them)")
    else:
        print("   Widget token (paste into the Scriptable script):    " + secrets_store.get("LEDGER_WIDGET_TOKEN"))
        print("   Apple Pay token (paste into the iPhone Shortcut):   " + secrets_store.get("LEDGER_HOOK_TOKEN"))
        print("   These only allow reading 3 numbers / adding a purchase — nothing else.")
    if secrets_store.get("LEDGER_DASHBOARD_PASSWORD"):
        print("   ✓ dashboard password set (for opening it from your phone over Tailscale)")
    elif not AUTO:
        if _yes("   Open the full dashboard from your phone over Tailscale? (sets a dashboard password)", True):
            while True:
                p1 = getpass.getpass("   Choose a dashboard password (12+ characters): ")
                if len(p1) < 12:
                    print("   too short")
                    continue
                if p1 != getpass.getpass("   Again: "):
                    print("   didn't match")
                    continue
                secrets_store.set("LEDGER_DASHBOARD_PASSWORD", p1, env)
                print("   ✓ set. Next: install Tailscale on laptop + iPhone, then run:  tailscale serve --bg 8765")
                break

    from .ai import provider
    print("\n5) Local AI: " + ("✓ Ollama is running — unknown bank emails are read on this PC" if provider(cfg) == "ollama" else
                              f"optional — install https://ollama.com then run:  ollama pull {cfg['ai'].get('ollama_model', 'qwen3:8b')}"))

    if _yes("\n6) Start Ledger automatically (login + after unlock, restarts if it crashes)?"):
        from .__main__ import autostart
        autostart(cfg)
    print("\nDone. Status any time:  python -m ledger status\n")
