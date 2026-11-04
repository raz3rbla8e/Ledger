"""Config loading. Everything lives in one folder on your PC (LEDGER_HOME, default ~/Ledger)."""
import os
import tomllib
from pathlib import Path

DEFAULT_CONFIG = """\
# ─────────────────────────────────────────────────────────────
#  Ledger config — lives only on this computer (never in git).
#  `python -m ledger setup` fills in the personal parts for you.
# ─────────────────────────────────────────────────────────────

[me]
name = ""
# Names your own e-transfers show up under. Money moving between these = internal transfer, not income/spending.
names = []
# Money you move to yourself FROM these banks counts as income, e.g. { RBC = "Government & Benefits" }
income_when_moved_from = {}

[email]
# Reads the alert emails your banks + Interac already send. Gmail *app passwords* — never bank logins.
enabled = true
backfill_since = "2025-09-01"
require_authenticated_senders = true   # ignore bank-looking emails that fail DKIM/SPF/DMARC (spoofs)

# [[email.inboxes]]
# address = "you@gmail.com"
# password_env = "LEDGER_IMAP_PASSWORD"

[files]
inbox = "inbox"
watch = ["~/Downloads"]           # bank CSV/OFX exports saved here are imported automatically

[sync]
every_minutes = 10                # fallback poll; new mail normally arrives instantly via IMAP IDLE
realtime = true

[server]
port = 8765
# Extra hostnames allowed to reach the dashboard, e.g. your Tailscale name "laptop.tailnet-1234.ts.net"
allowed_hosts = []

[ai]
#   "auto" → local Ollama if running (nothing leaves your PC), otherwise off · "ollama" · "anthropic" · "none"
provider = "auto"
ollama_url = "http://localhost:11434"
ollama_model = "qwen3:8b"
anthropic_model = "claude-haiku-5-5"
chat = "local"

[applepay]
cards = {}                        # Wallet card name → account id, e.g. { "Tangerine" = "tangerine-cc-1234" }

# [[people]]
# name = "LANDLORD NAME"
# outgoing = { category = "Rent" }

[plan]
savings_goal_pct = 20
cash_buffer = 500
auto_budgets = true
horizon = ""
events = []

[[goals]]
name = "Emergency fund"
target = "3x_fixed"
priority = 1

[notify]
email = true
telegram = true
large_purchase = 150
morning_brief = "08:30"
weekly_report = "sun 19:00"
monthly_report = true
quiet_hours = "23:00-08:00"
"""


def home() -> Path:
    p = Path(os.environ.get("LEDGER_HOME", Path.home() / "Ledger")).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_env(h: Path):
    env = h / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.split(" #")[0].strip().strip('"').strip("'")
                if v:
                    os.environ.setdefault(k.strip(), v)


ENV_TEMPLATE = """# Secrets. On Windows/macOS, `python -m ledger setup` stores these in the OS keychain instead and leaves this
# file blank — anything you type here is moved into the keychain on the next start.
# Gmail app passwords: https://myaccount.google.com/apppasswords  (one per inbox)
LEDGER_IMAP_PASSWORD=
LEDGER_IMAP_PASSWORD_2=
# Set if the dashboard is reachable from other devices (phone via Tailscale)
LEDGER_DASHBOARD_PASSWORD=
# Secret for the Apple Pay shortcut (any long random string; put the same one in the Shortcut)
LEDGER_HOOK_TOKEN=
# Telegram bot for alerts + chat
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
# Optional — only if [ai] provider/chat use Claude
ANTHROPIC_API_KEY=
"""


def load() -> dict:
    h = home()
    cfg_path = h / "config.toml"
    if not cfg_path.exists():
        cfg_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    env = h / ".env"
    if not env.exists():
        env.write_text(ENV_TEMPLATE, encoding="utf-8")
        try:
            os.chmod(env, 0o600)
        except OSError:
            pass
    from . import secrets_store
    secrets_store.migrate_env_file(env)
    load_env(h)
    secrets_store.load_into_env()
    with open(cfg_path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_home"] = h
    cfg.setdefault("me", {}).setdefault("names", [])
    cfg.setdefault("email", {})
    cfg.setdefault("files", {}).setdefault("inbox", "inbox")
    cfg["files"].setdefault("watch", [])
    cfg.setdefault("sync", {}).setdefault("every_minutes", 10)
    cfg["sync"].setdefault("realtime", True)
    cfg.setdefault("server", {}).setdefault("port", 8765)
    cfg["server"].setdefault("allowed_hosts", [])
    cfg["email"].setdefault("require_authenticated_senders", True)
    cfg.setdefault("ai", {}).setdefault("provider", "auto")
    cfg["ai"].setdefault("chat", "local")
    cfg.setdefault("accounts", {})
    cfg.setdefault("people", [])
    cfg.setdefault("merchants", [])
    cfg.setdefault("pin", [])
    cfg.setdefault("applepay", {}).setdefault("cards", {})
    p = cfg.setdefault("plan", {})
    p.setdefault("savings_goal_pct", 20)
    p.setdefault("cash_buffer", 500)
    p.setdefault("auto_budgets", True)
    p.setdefault("events", [])
    p.setdefault("horizon", "")
    cfg.setdefault("goals", [])
    p.setdefault("cancelled", [])
    from . import state
    state.CANCELLED = {str(c).strip().lower() for c in p["cancelled"]}
    state.FIXED_TOLD = {k: float(v) for k, v in (p.get("fixed") or {}).items()}
    cfg.setdefault("notify", {})
    inbox = h / cfg["files"]["inbox"]
    inbox.mkdir(parents=True, exist_ok=True)
    cfg["_inbox"] = inbox
    return cfg


def my_names(cfg) -> set:
    return {n.strip().upper() for n in cfg["me"]["names"] if n.strip()}
