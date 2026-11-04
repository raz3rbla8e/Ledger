"""Nightly local backups of the database (SQLite online backup — safe while Ledger is running). Keeps 14 days."""
from datetime import date
import sqlite3


def nightly(con, cfg, log=print, keep=14):
    folder = cfg["_home"] / "backups"
    folder.mkdir(exist_ok=True)
    dest = folder / f"ledger-{date.today().isoformat()}.db"
    if dest.exists():
        return None
    out = sqlite3.connect(dest)
    try:
        con.backup(out)
    finally:
        out.close()
    olds = sorted(folder.glob("ledger-*.db"))
    for f in olds[:-keep]:
        try:
            f.unlink()
        except OSError:
            pass
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('last_backup', ?)", (dest.name,))
    con.commit()
    log(f"backup → {dest}")
    return dest
