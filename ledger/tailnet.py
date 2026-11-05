"""Phone access through Tailscale, set up automatically.

Ledger keeps listening only on this computer (127.0.0.1). Tailscale Serve puts it at a private HTTPS address that
only devices signed in to *your* Tailscale account can reach (https://<this-pc>.<your-tailnet>.ts.net). Nothing is
opened to the internet, and there's no port forwarding.

Serve also tells Ledger who is asking (the Tailscale-User-Login header). Tailscale removes any copy of that header a
client tries to send and writes the real one, so Ledger can trust it — but only because Ledger itself is reachable
from nowhere but this computer. Funnel (public) traffic never carries it, so it would still need the password."""
import json, os, re, shutil, subprocess, threading, time
from datetime import datetime

NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
STATE = {"installed": False, "running": False, "url": None, "serving": False, "enable_url": None, "error": None, "checked": None}


def find():
    t = shutil.which("tailscale")
    if t:
        return t
    for p in (r"C:\Program Files\Tailscale\tailscale.exe", r"C:\Program Files (x86)\Tailscale\tailscale.exe",
              "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if os.path.exists(p):
            return p
    return None


def _run(exe, *args, timeout=20):
    return subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, creationflags=NOWIN)


def refresh(port, log=print, setup=True):
    """Check Tailscale, and if it's signed in but not yet serving Ledger, turn Serve on for Ledger's port."""
    exe = find()
    STATE.update(installed=bool(exe), checked=datetime.utcnow().isoformat(timespec="seconds"), error=None)
    if not exe:
        STATE.update(running=False, url=None, serving=False)
        return STATE
    try:
        st = json.loads(_run(exe, "status", "--json").stdout or "{}")
    except Exception as e:
        STATE.update(running=False, error=f"couldn't ask Tailscale: {e}")
        return STATE
    running = st.get("BackendState") == "Running"
    dns = ((st.get("Self") or {}).get("DNSName") or "").rstrip(".")
    STATE.update(running=running, url=f"https://{dns}" if dns else None)
    if not running:
        STATE.update(serving=False)
        return STATE
    target = f"127.0.0.1:{port}"
    try:
        cur = _run(exe, "serve", "status", "--json").stdout or "{}"
    except Exception:
        cur = "{}"
    if target in cur:
        STATE.update(serving=True, enable_url=None)
        return STATE
    if not setup:
        return STATE
    try:
        r = _run(exe, "serve", "--bg", f"http://{target}", timeout=25)
        out = (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode(errors="ignore") if isinstance(e.stdout, bytes) else (e.stdout or "")) + \
              ((e.stderr or b"").decode(errors="ignore") if isinstance(e.stderr, bytes) else (e.stderr or ""))
    m = re.search(r"https://login\.tailscale\.com/\S+", out)
    if m:  # first time only: HTTPS for your tailnet has to be switched on once, in the Tailscale admin page
        STATE.update(serving=False, enable_url=m.group(0), error="Tailscale needs HTTPS switched on once for your account")
        return STATE
    try:
        cur = _run(exe, "serve", "status", "--json").stdout or "{}"
    except Exception:
        cur = "{}"
    STATE.update(serving=target in cur, enable_url=None, error=None if target in cur else (out.strip()[:300] or "Serve didn't start"))
    if STATE["serving"]:
        log(f"Phone access on: {STATE['url']}")
    return STATE


def loop(port, log=print):
    """Re-check every 10 minutes, so installing or signing in to Tailscale later just starts working."""
    while True:
        try:
            refresh(port, log)
        except Exception as e:
            STATE.update(error=str(e)[:200])
        time.sleep(600 if STATE.get("serving") else 120)


def start(port, log=print):
    threading.Thread(target=loop, args=(port, log), name="tailscale", daemon=True).start()
