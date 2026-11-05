"""Self-update: fast-forwards from origin, runs the tests, rolls back when they fail, refuses to touch local edits.
(Not in the updater's own test list — it runs the suite itself.)"""
import sys as _s; [x.reconfigure(encoding="utf-8", errors="replace") for x in (_s.stdout, _s.stderr) if hasattr(x, "reconfigure")]  # Windows pipes default to cp1252
import os, subprocess, sys, tempfile, shutil, sqlite3
from pathlib import Path
src = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
def sh(*a, cwd):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
work = tmp / "work"; shutil.copytree(src, work, ignore=shutil.ignore_patterns(".git", "__pycache__"))
for c in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "v1"]):
    sh(*c, cwd=work)
sh("git", "clone", "-q", "--bare", str(work), str(tmp / "origin.git"), cwd=tmp)
sh("git", "clone", "-q", str(tmp / "origin.git"), str(tmp / "laptop"), cwd=tmp)
laptop = tmp / "laptop"
probe = f"""
import sys, sqlite3; sys.path.insert(0, r'{laptop}')
from ledger import updater
updater.restart = lambda log=print, delay=0: print('RESTART')
con = sqlite3.connect(':memory:'); con.row_factory = sqlite3.Row
con.execute("CREATE TABLE messages(key TEXT PRIMARY KEY, title TEXT, body TEXT, push INT, created TEXT, sent INT DEFAULT 0)")
print(updater.check(con, {{}}, log=lambda *a: None))
"""
run = lambda: subprocess.run([sys.executable, "-c", probe], cwd=laptop, capture_output=True, text=True, env={**os.environ, "LEDGER_NO_KEYCHAIN": "1"}).stdout
assert "up to date" in run()
# a good change → updated + restart
(work / "ledger" / "_probe.py").write_text("X = 1\n")
sh("git", "add", "-A", cwd=work); sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "Add probe", cwd=work); sh("git", "push", "-q", str(tmp / "origin.git"), "main", cwd=work)
out = run(); assert "updated" in out and "RESTART" in out, out
assert (laptop / "ledger" / "_probe.py").exists()
# a broken change → held back, laptop stays on the working version
good = sh("git", "rev-parse", "HEAD", cwd=laptop)
(work / "ledger" / "planner.py").write_text((work / "ledger" / "planner.py").read_text() + "\nraise SystemExit('broken')\n")
sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "Break it", cwd=work); sh("git", "push", "-q", str(tmp / "origin.git"), "main", cwd=work)
out = run(); assert "held back" in out and "RESTART" not in out, out
assert sh("git", "rev-parse", "HEAD", cwd=laptop) == good
# hand edits in the folder → never overwritten
(work / "ledger" / "planner.py").write_text((src / "ledger" / "planner.py").read_text())
sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "Fix it", cwd=work); sh("git", "push", "-q", str(tmp / "origin.git"), "main", cwd=work)
(laptop / "ledger" / "notify.py").write_text((laptop / "ledger" / "notify.py").read_text() + "\n# my tweak\n")
out = run(); assert "edited by hand" in out, out
print("updater OK")
