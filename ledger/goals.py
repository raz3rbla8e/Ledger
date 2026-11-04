"""Goals funded automatically, in priority order, from cash above your buffer."""
from datetime import date


def sync_goals(con, cfg):
    """Config goals → goals table (dashboard edits to target/priority survive; new config goals are added)."""
    for g in cfg.get("goals", []):
        t = g.get("target")
        auto = t if isinstance(t, str) else None
        con.execute("""INSERT INTO goals(name, target, due, priority, auto, note) VALUES (?,?,?,?,?,?)
                       ON CONFLICT(name) DO UPDATE SET auto=excluded.auto""",
                    (g["name"], None if auto else float(t or 0), g.get("due"), g.get("priority", 5), auto, g.get("note")))
    con.commit()


def goals_status(con, cfg, fc=None, safe=None):
    from .balances import cash_position
    from .forecast import forecast
    from .planner import safe_to_spend
    sync_goals(con, cfg)
    pos = cash_position(con)
    fc = fc or forecast(con, cfg)
    safe = safe or safe_to_spend(con, cfg)
    buffer = float(cfg["plan"].get("cash_buffer", 500))
    available = max(0.0, pos["cash"] - buffer) if pos["complete"] else None
    today = date.today()
    out = []
    for g in con.execute("SELECT * FROM goals WHERE active=1 ORDER BY priority, id"):
        target = g["target"] or 0
        why = None
        if g["auto"] == "3x_fixed":
            target = round(3 * safe["fixed_monthly"], -1)
            why = f"3 months of fixed costs ({safe['fixed_monthly']:,.0f}/mo)"
        elif g["auto"] == "runway":
            # cash you need *today* so the forecast never dips under your buffer before the horizon
            rel_low = fc["low"]["balance"] - fc["start"]
            target = round(-rel_low, -1) if rel_low < 0 else 0
            from .insights import nice
            why = f"cash needed to cover the forecast low point ({nice(fc['low']['date'])})"
        funded = None
        if available is not None:
            funded = min(target, available)
            available -= funded
        months_left = None
        per_month = None
        if g["due"]:
            dd = date.fromisoformat(g["due"])
            months_left = max(0.0, (dd - today).days / 30.44)
            if funded is not None and target > funded:
                per_month = round((target - funded) / max(months_left, 0.5), 2)
        out.append(dict(id=g["id"], name=g["name"], target=round(target, 2), funded=None if funded is None else round(funded, 2),
                        pct=None if funded is None or not target else round(funded / target, 3), due=g["due"], priority=g["priority"],
                        per_month=per_month, months_left=None if months_left is None else round(months_left, 1), why=why,
                        done=funded is not None and target > 0 and funded >= target))
    return dict(goals=out, needs_balances=not pos["complete"], unallocated=None if available is None else round(available, 2))
