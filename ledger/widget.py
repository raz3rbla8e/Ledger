"""Tiny read-only payload for the phone widget — just the numbers, no transaction details."""
from datetime import date

from . import planner
from .insights import nice, recurring


def widget_payload(con, cfg):
    s = planner.safe_to_spend(con, cfg)
    t = date.today().isoformat()
    spent_today, _ = planner.spend_between(con, t, t, flexible_only=True)
    nxt = sorted([r for r in recurring(con) if r["active"] and r["next_expected"] and r["next_expected"] >= t],
                 key=lambda r: r["next_expected"])[:1]
    tone = "bad" if s["safe_to_spend"] < 0 else "warn" if s.get("plan_gap") or s["per_day"] < 15 else "good"
    return {
        "today": round(max(0, s["today_allowance"] - spent_today), 2),
        "today_allowance": s["today_allowance"],
        "spent_today": spent_today,
        "left_month": s["safe_to_spend"],
        "per_day": s["per_day"],
        "days_left": s["days_left"],
        "tone": tone,
        "next_bill": {"name": nxt[0]["merchant"], "amount": nxt[0]["amount"], "date": nice(nxt[0]["next_expected"], False)} if nxt else None,
        "updated": date.today().isoformat(),
    }
