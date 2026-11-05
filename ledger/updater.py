"""Keeps Ledger up to date by itself.

Every half hour (and when you press "Check for updates") it asks GitHub whether the branch has moved. If it has:
  1. fast-forward only — it never overwrites your own local edits or merges anything odd
  2. run the test suite against the new code in a throwaway folder (your real data is never touched)
  3. tests fail → roll straight back to the version that was running, and tell you
  4. tests pass → restart itself on the new version, and tell you what changed

Uses git when it can find it (on PATH, or the copy bundled with GitHub Desktop). Without git it downloads the
branch as a zip from GitHub over HTTPS and replaces the `ledger/` package the same careful way."""
import glob, io, json, os, shutil, subprocess, sys, tempfile, threading, time, urllib.request, zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ["tests/test_pipeline.py", "tests/test_v3.py", "tests/test_v4.py", "tests/test_idle.py"]
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_lock = threading.Lock()
STATE = {"checked": None, "status": "not checked yet", "version": None, "error": None}


def _settings(cfg):
    u = cfg.setdefault("updates", {})
    u.setdefault("auto", True)
    u.setdefault("every_minutes", 30)
    u.setdefault("branch", "main")
    u.setdefault("repo", "raz3rbla8e/Ledger")
    return u


def find_git():
    g = shutil.which("git")
    if g:
        return g
    if os.name == "nt":
        la = os.environ.get("LOCALAPPDATA", "")
        found = sorted(glob.glob(os.path.join(la, "GitHubDesktop", "app-*", "resources", "app", "git", "cmd", "git.exe")))
        for p in (found[-1:] + [r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe"]):
            if p and os.path.exists(p):
                return p
    return None


def _git(git, *args, timeout=90):
    r = subprocess.run([git, "-c", f"safe.directory={ROOT.as_posix()}", "-c", "core.fileMode=false", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout, creationflags=NOWIN)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def current_version():
    git = find_git()
    if git and (ROOT / ".git").exists():
        try:
            sha, when, subj = _git(git, "log", "-1", "--format=%h%x09%cs%x09%s").split("\t", 2)
            return dict(sha=sha, date=when, subject=subj, via="git")
        except Exception:
            pass
    f = ROOT / "ledger" / ".version"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return dict(sha="unknown", date=None, subject=None, via="git" if git else "zip")


def run_tests(log=print):
    """Runs the test suite with a temporary home, so it never sees your real database, config or passwords."""
    tmp = tempfile.mkdtemp(prefix="ledger-update-test-")
    env = {**os.environ, "LEDGER_HOME": tmp, "LEDGER_NO_KEYCHAIN": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    for k in [k for k in env if k.startswith("LEDGER_") and k.endswith(("_APP_PASSWORD", "_TOKEN", "_PASSWORD")) and k != "LEDGER_HOME"]:
        env.pop(k)  # the tests never need real secrets
    py = sys.executable
    if py.lower().endswith("pythonw.exe") and os.path.exists(py[:-5] + ".exe"):
        py = py[:-5] + ".exe"
    try:
        for t in TESTS:
            if not (ROOT / t).exists():
                continue
            r = subprocess.run([py, t], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300, creationflags=NOWIN)
            if r.returncode:
                return False, f"{t} failed:\n{(r.stdout + r.stderr)[-800:]}"
        r = subprocess.run([py, "-c", "import ledger.server, ledger.api, ledger.sync, ledger.planner, ledger.assistant"],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, creationflags=NOWIN)
        if r.returncode:
            return False, "the new version doesn't start:\n" + r.stderr[-800:]
        return True, ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _tell(con, cfg, key, title, body):
    try:
        from .notify import _queue
        _queue(con, key, title, body, True)
        con.commit()
    except Exception:
        pass


def _update_git(git, u, con, cfg, log):
    br = u["branch"]
    _git(git, "fetch", "--quiet", "origin", br, timeout=120)
    here, there = _git(git, "rev-parse", "HEAD"), _git(git, "rev-parse", f"origin/{br}")
    if here == there:
        return "up to date", None
    try:
        _git(git, "merge-base", "--is-ancestor", "HEAD", f"origin/{br}")
    except RuntimeError:
        return "skipped: this copy has its own commits GitHub doesn't have", None
    if _git(git, "status", "--porcelain", "--untracked-files=no"):
        return "skipped: files in the Ledger folder were edited by hand (commit or discard them in GitHub Desktop)", None
    log_lines = _git(git, "log", "--format=• %s", f"HEAD..origin/{br}").splitlines()
    _git(git, "merge", "--ff-only", "--quiet", f"origin/{br}")
    ok, why = run_tests(log)
    if not ok:
        _git(git, "reset", "--hard", "--quiet", here)
        _tell(con, cfg, f"upd-fail:{there[:12]}", "⚠️ Update held back",
              f"A new version of Ledger is on GitHub but its tests failed, so Ledger stayed on the version that works.\n{why[:600]}")
        return f"held back: tests failed on {there[:7]}", None
    return f"updated {here[:7]} → {there[:7]}", log_lines


def _update_zip(u, con, cfg, log):
    req = urllib.request.Request(f"https://api.github.com/repos/{u['repo']}/commits/{u['branch']}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "ledger-updater"})
    with urllib.request.urlopen(req, timeout=30) as r:
        info = json.load(r)
    sha = info["sha"]
    mine = current_version().get("full")
    if mine == sha:
        return "up to date", None
    with urllib.request.urlopen(urllib.request.Request(f"https://codeload.github.com/{u['repo']}/zip/{sha}",
                                                       headers={"User-Agent": "ledger-updater"}), timeout=120) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    top = z.namelist()[0].split("/")[0]
    new = ROOT / "ledger.new"
    old = ROOT / "ledger.previous"
    shutil.rmtree(new, ignore_errors=True)
    for n in z.namelist():
        rel = n[len(top) + 1:]
        if not rel or n.endswith("/") or not (rel.startswith("ledger/") or rel.startswith("tests/")):
            continue
        dest = (new if rel.startswith("ledger/") else ROOT) / (rel[len("ledger/"):] if rel.startswith("ledger/") else rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(z.read(n))
    (new / ".version").write_text(json.dumps(dict(sha=sha[:7], full=sha, date=info["commit"]["committer"]["date"][:10],
                                                  subject=info["commit"]["message"].splitlines()[0], via="zip")))
    shutil.rmtree(old, ignore_errors=True)
    (ROOT / "ledger").rename(old)
    new.rename(ROOT / "ledger")
    ok, why = run_tests(log)
    if not ok:
        shutil.rmtree(ROOT / "ledger", ignore_errors=True)
        old.rename(ROOT / "ledger")
        _tell(con, cfg, f"upd-fail:{sha[:12]}", "⚠️ Update held back",
              f"A new version of Ledger is on GitHub but its tests failed, so Ledger stayed on the version that works.\n{why[:600]}")
        return f"held back: tests failed on {sha[:7]}", None
    return f"updated → {sha[:7]}", ["• " + info["commit"]["message"].splitlines()[0]]


def check(con, cfg, log=print, force=False):
    """Look for a new version; install it if it passes the tests. Returns a short status line."""
    u = _settings(cfg)
    if not (u["auto"] or force):
        return "auto-update is off"
    if not _lock.acquire(blocking=False):
        return "already checking"
    try:
        git = find_git()
        if git and (ROOT / ".git").exists():
            status, changes = _update_git(git, u, con, cfg, log)
        else:
            status, changes = _update_zip(u, con, cfg, log)
        STATE.update(checked=datetime.utcnow().isoformat(timespec="seconds"), status=status, error=None, version=current_version())
        if changes is not None:
            log(f"update: {status}")
            v = STATE["version"]
            _tell(con, cfg, f"upd:{v.get('sha')}", "⬆️ Ledger updated",
                  "Ledger updated itself and restarted. What's new:\n" + "\n".join(changes[:8]))
            restart(log)
        return status
    except Exception as e:
        STATE.update(checked=datetime.utcnow().isoformat(timespec="seconds"), status="couldn't check", error=str(e)[:300])
        log(f"update check failed: {e}")
        return "couldn't check: " + str(e)[:200]
    finally:
        _lock.release()


def restart(log=print, delay=1.5):
    """Start a fresh Ledger on the new code, then step aside. The new one waits for the port to free up."""
    log("restarting on the new version…")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    kw = dict(cwd=str(ROOT), close_fds=True, env={**os.environ, "LEDGER_RESTARTED": "1"})
    if os.name == "nt":
        kw["creationflags"] = flags
    else:
        kw["start_new_session"] = True
    subprocess.Popen([sys.executable, "-m", "ledger", "run", "--no-browser"], **kw)

    def bye():
        time.sleep(delay)
        os._exit(0)
    threading.Thread(target=bye, daemon=True).start()


def loop(con, cfg, lock, log=print):
    """Background thread: first check a few minutes after start, then every `every_minutes`."""
    time.sleep(180)
    while True:
        u = _settings(cfg)
        if u["auto"]:
            with lock:
                check(con, cfg, log)
        time.sleep(max(5, int(u["every_minutes"])) * 60)
