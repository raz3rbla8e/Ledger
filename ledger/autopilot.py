"""Things Ledger does on its own after every sync — no approval needed. Everything it does is logged to the activity feed."""
from datetime import date, timedelta

from .db import log_activity


def run(con, cfg, today=None, log=print):
    today = today or date.today()
    done = []
    # 1) trip mode: tag everything during a trip so it's tracked separately and doesn't skew your normal averages
    for e in cfg["plan"].get("events", []):
        if e.get("kind") != "trip":
            continue
        tag = f"trip:{e['name']}"
        n = con.execute("""UPDATE transactions SET tags = CASE WHEN tags IS NULL OR tags='' THEN ? ELSE tags || ',' || ? END
                           WHERE date BETWEEN ? AND ? AND kind IN ('expense','refund') AND COALESCE(tags,'') NOT LIKE ?""",
                        (tag, tag, e["start"], e["end"], f"%{tag}%")).rowcount
        if n:
            done.append(f"Tagged {n} transaction(s) to {e['name']}")
        if e["start"] <= today.isoformat() <= e["end"]:
            log_activity(con, "trip", f"Trip mode on for {e['name']} — spending is tracked separately until {e['end']}", f"tripon:{e['name']}")
    # 2) balance cross-checks from low-balance alerts
    from .balances import drift_checks
    for d in drift_checks(con):
        log_activity(con, "balance", f"{d['account']}: bank says under ${d['max']:,.0f} on {d['date']} but estimate was ${d['estimate']:,.0f} — "
                                     "re-anchor it (drop a statement in Downloads or set the balance once)", f"drift:{d['account']}:{d['date']}")
    # 3) monthly housekeeping
    if today.day <= 3:
        from .planner import budgets
        bs = [b for b in budgets(con, cfg) if b["auto"]]
        if bs:
            log_activity(con, "budget", f"Re-learned {len(bs)} budgets for {today:%B} from your last 3 months", f"rebudget:{today:%Y-%m}")
    for t in done:
        log_activity(con, "auto", t, f"{t}:{today}")
    con.commit()
    return done
