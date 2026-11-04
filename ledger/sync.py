import json
import traceback

from .ai import ai_fill
from .categorize import Categorizer, recategorize_all
from .db import log_activity, source_fail, source_ok
from .ingest_email import retry_unparsed, sync_email
from .ingest_files import sync_files, watch_folders
from .store import pair_transfers


def run_sync(con, cfg, log=print):
    summary = {}
    before = con.execute("SELECT COALESCE(max(id),0) FROM transactions").fetchone()[0]
    cz = Categorizer(con, cfg)
    try:
        summary["email"] = sync_email(con, cfg, cz, log)
    except Exception as e:
        log(f"  email: error — {e}")
        summary["email_error"] = str(e)
        traceback.print_exc()
    con.commit()
    try:
        retry_unparsed(con, cfg, cz, summary.setdefault("email", {}) or {})
    except Exception as e:
        log(f"  retry: {e}")
    for name, fn in (("files", sync_files), ("watch", watch_folders)):
        try:
            summary[name] = fn(con, cfg, cz, log)
            source_ok(con, f"folder:{name}", "files", (summary[name] or {}).get("new", 0))
        except Exception as e:
            log(f"  {name}: error — {e}")
            source_fail(con, f"folder:{name}", "files", e)
    if ai_fill(con, cfg, log):
        summary["ai_recategorized"] = recategorize_all(con, cfg)
    summary["transfers_linked"] = pair_transfers(con)
    new = con.execute("SELECT count(*), sum(reviewed=0) FROM transactions WHERE id>?", (before,)).fetchone()
    if new[0]:
        log_activity(con, "sort", f"Pulled in and sorted {new[0]} new transaction{'s' if new[0] > 1 else ''}"
                                  + (f" — {new[1]} need a quick look" if new[1] else " — all categorized automatically"))
    try:
        from .autopilot import run as autopilot
        autopilot(con, cfg, log=log)
    except Exception as e:
        log(f"  autopilot: error — {e}")
        traceback.print_exc()
    try:
        from .notify import detect, flush
        detect(con, cfg)
        flush(con, cfg, log)
    except Exception as e:
        log(f"  notify: error — {e}")
        traceback.print_exc()
    con.execute("INSERT INTO sync_log(summary) VALUES (?)", (json.dumps(summary),))
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('data_version', COALESCE((SELECT value FROM meta WHERE key='data_version'),0)+1)")
    con.commit()
    return summary
