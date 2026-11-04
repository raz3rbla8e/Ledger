"""ledger — a private, local, self-running money manager.

  python -m ledger setup        guided setup: personal profile, app passwords (tested), Telegram, phone, autostart
  python -m ledger run          dashboard + real-time email + background jobs (what autostart launches)
  python -m ledger status       is it running? last sync, inbox health, last backup
  python -m ledger sync         pull new email alerts + import statements, once
  python -m ledger ask "how much on food this month?"
  python -m ledger report       this week's report, printed
  python -m ledger mcp          Ledger as a (read-only) MCP server for Claude Desktop / Claude Code
  python -m ledger accounts     list account ids
  python -m ledger recat        re-run categorization over history
  python -m ledger autostart    start `ledger run` at login (and restart it if it ever crashes)
  python -m ledger demo         try it on a fake year of data (separate database)
"""
import argparse
import os
import platform
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from . import config, db


def _con(cfg):
    return db.connect(cfg["_home"] / "ledger.db")


def _port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _run_key(command):
    """Add (or with None, remove) Ledger from HKCU\\...\\Run — the per-user 'start at login' list."""
    import winreg
    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
    try:
        if command:
            winreg.SetValueEx(k, "Ledger", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(k, "Ledger")
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(k)


def autostart(cfg):
    py = Path(sys.executable)
    root = Path(__file__).resolve().parent.parent
    home = cfg["_home"]
    sysname = platform.system()
    if sysname == "Windows":
        pyw = py.with_name("pythonw.exe")
        exe = pyw if pyw.exists() else py
        # Triggers scoped to *this* user — an unscoped logon trigger means "any user" and needs admin ("Access is denied").
        user = xml_escape(f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\"))
        # Task Scheduler: start at login AND on unlock (after sleep), restart every minute if it crashes, run on battery.
        xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Ledger — private money manager</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId></LogonTrigger>
    <SessionStateChangeTrigger><Enabled>true</Enabled><StateChange>SessionUnlock</StateChange><UserId>{user}</UserId></SessionStateChangeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>{user}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{exe}</Command><Arguments>-m ledger run --no-browser</Arguments><WorkingDirectory>{root}</WorkingDirectory></Exec>
  </Actions>
</Task>"""
        f = home / "ledger-task.xml"
        f.write_text(xml, encoding="utf-16")
        r = subprocess.run(["schtasks", "/Create", "/TN", "Ledger", "/XML", str(f), "/F"], capture_output=True, text=True)
        old = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Ledger.vbs"
        if old.exists():
            old.unlink()
        if r.returncode == 0:
            _run_key(None)
            subprocess.run(["schtasks", "/Run", "/TN", "Ledger"], capture_output=True)
            print("✓ Ledger now starts at login and after unlocking, and restarts itself if it crashes.\n"
                  "  Undo: schtasks /Delete /TN Ledger /F")
        else:
            # Fallback that never needs admin: the per-user Run key (starts at login; no crash-restart).
            _run_key(f'"{exe}" -c "import sys, runpy; sys.path.insert(0, r\'{root}\'); runpy.run_module(\'ledger\', run_name=\'__main__\')" run --no-browser')
            print("✓ Ledger now starts when you log in (Task Scheduler said: "
                  f"{(r.stderr or r.stdout).strip() or 'error'} — used the per-user startup list instead).")
        port = cfg["server"]["port"]
        if r.returncode != 0 and not _port_in_use(port):
            subprocess.Popen([str(exe), "-m", "ledger", "run", "--no-browser"], cwd=str(root), close_fds=True,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        for _ in range(30):  # wait for the dashboard to come up (the first email backfill runs in the background)
            if _port_in_use(port):
                break
            time.sleep(0.5)
    elif sysname == "Darwin":
        f = Path.home() / "Library/LaunchAgents/com.ledger.local.plist"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.ledger.local</string>
<key>ProgramArguments</key><array><string>{py}</string><string>-m</string><string>ledger</string><string>run</string><string>--no-browser</string></array>
<key>WorkingDirectory</key><string>{root}</string>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>""")
        subprocess.run(["launchctl", "load", "-w", str(f)])
        print(f"✓ Installed {f} (runs at login, restarts if it crashes).")
    else:
        f = Path.home() / ".config/systemd/user/ledger.service"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"""[Unit]
Description=Ledger
[Service]
WorkingDirectory={root}
ExecStart={py} -m ledger run --no-browser
Restart=always
RestartSec=30
[Install]
WantedBy=default.target
""")
        subprocess.run(["systemctl", "--user", "daemon-reload"])
        subprocess.run(["systemctl", "--user", "enable", "--now", "ledger.service"])
        print(f"✓ Installed {f}")


def status(cfg, con):
    port = cfg["server"]["port"]
    print(f"Running: {'yes' if _port_in_use(port) else 'NO'}  (dashboard http://127.0.0.1:{port})")
    last = con.execute("SELECT at FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    print(f"Last sync: {last['at'] + ' UTC' if last else 'never'}")
    for s in con.execute("SELECT * FROM sources ORDER BY key"):
        state = "OK" if not s["fail_streak"] else f"FAILING ×{s['fail_streak']}: {s['last_error']}"
        print(f"  {s['key']:<42} {state}")
    b = con.execute("SELECT value FROM meta WHERE key='last_backup'").fetchone()
    print(f"Last backup: {b['value'] if b else 'none yet'}")
    print(f"Log file: {cfg['_home'] / 'logs' / 'ledger.log'}")
    return 0 if _port_in_use(port) else 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # Windows consoles: don't crash on ✓ / emoji; pythonw has no console at all
        try:
            if stream:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="ledger", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="run",
                    choices=["run", "setup", "status", "sync", "serve", "accounts", "recat", "autostart", "demo", "ask", "report", "mcp", "import"])
    ap.add_argument("rest", nargs="*", help="question for `ask`")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--auto", action="store_true", help="setup: no questions — keep what works, accept defaults")
    a = ap.parse_args(argv)

    if a.cmd == "demo":
        from .demo import build
        from .server import serve
        os.environ["LEDGER_HOME"] = str(Path(os.environ.get("LEDGER_HOME", Path.home() / "Ledger")) / "demo")
        cfg = config.load()
        con = _con(cfg)
        build(con, cfg)
        port = a.port or 8766
        print(f"Demo ready → http://127.0.0.1:{port}")
        if not a.no_browser:
            webbrowser.open(f"http://127.0.0.1:{port}")
        return serve(con, cfg, a.host, port)

    cfg = config.load()
    port = a.port or cfg["server"]["port"]
    if a.cmd == "mcp":
        from .mcp_server import serve_stdio
        return serve_stdio(cfg)
    con = _con(cfg)
    if a.cmd == "setup":
        from .setup_wizard import run as setup
        return setup(cfg, auto=a.auto)
    if a.cmd == "status":
        sys.exit(status(cfg, con))
    if a.cmd == "sync":
        from .sync import run_sync
        print(run_sync(con, cfg))
    elif a.cmd == "accounts":
        for r in con.execute("SELECT * FROM accounts ORDER BY id"):
            n = con.execute("SELECT count(*) FROM transactions WHERE account_id=?", (r["id"],)).fetchone()[0]
            print(f"{r['id']:<28} {r['name']:<36} {n:>5} txns")
    elif a.cmd == "ask":
        from .assistant import answer
        print(answer(con, cfg, " ".join(a.rest) or "/help"))
    elif a.cmd == "report":
        from .notify import weekly_report
        print(weekly_report(con, cfg))
    elif a.cmd == "recat":
        from .categorize import recategorize_all
        print(f"{recategorize_all(con, cfg)} transactions re-categorized")
    elif a.cmd == "import":  # python -m ledger import <Takeout .zip / .mbox>
        from .categorize import Categorizer
        from .ingest_mbox import import_mail_export
        from .store import pair_transfers
        for f in a.rest:
            import_mail_export(con, cfg, Categorizer(con, cfg), f)
        print(f"{pair_transfers(con)} transfers between your accounts linked")
        con.commit()
    elif a.cmd == "autostart":
        autostart(cfg)
    else:
        from . import logs
        from .server import serve
        lg = logs.setup(cfg["_home"], console=sys.stderr is not None)
        log = lg.info
        if _port_in_use(port):
            log(f"Ledger is already running on port {port} — nothing to do.")
            return
        log(f"Ledger starting (home: {cfg['_home']})")
        from .sync import apply_config_changes
        apply_config_changes(con, cfg, log)
        if not a.no_browser:
            webbrowser.open(f"http://127.0.0.1:{port}")
        serve(con, cfg, a.host, port, sync_every=cfg["sync"]["every_minutes"] if a.cmd == "run" else None, log=log)


def _crash_log():
    """A background (pythonw) start has no window, so anything that kills it before logging is set up lands here."""
    import traceback
    from datetime import datetime
    try:
        d = Path(os.environ.get("LEDGER_HOME", Path.home() / "Ledger")).expanduser() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "crash.log", "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S}  {' '.join(sys.argv)}\n{traceback.format_exc()}")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException:
        _crash_log()
        raise
