"""Ledger as a local MCP server (stdio) for Claude Desktop / Claude Code — read-only by design.

Claude Desktop → Settings → Developer → Edit config, add:
  "mcpServers": { "ledger": { "command": "python", "args": ["-m", "ledger", "mcp"], "cwd": "C:\\\\Users\\\\you\\\\Ledger-app" } }

Nothing here can move money, change categories, or touch your inboxes. Note: what Claude reads through these
tools is sent to Claude like anything else you share in a chat.
"""
import json
import sys
import threading
from datetime import date

from . import db
from . import insights as ins

TOOLS = [
    dict(name="money_today", description="What you can safely spend today and for the rest of the month, plus the one-line status.",
         inputSchema={"type": "object", "properties": {}}),
    dict(name="spending_summary", description="Income, spending, savings rate, spending by category and top merchants for a month.",
         inputSchema={"type": "object", "properties": {"month": {"type": "string", "description": "YYYY-MM, default this month"}}}),
    dict(name="search_transactions", description="Search transactions by text, category and/or month. Amounts: negative = money out.",
         inputSchema={"type": "object", "properties": {"query": {"type": "string"}, "category": {"type": "string"},
                                                       "month": {"type": "string"}, "limit": {"type": "integer", "maximum": 200}}}),
    dict(name="cash_forecast", description="Projected cash month by month to the plan horizon, with optional what-if income.",
         inputSchema={"type": "object", "properties": {"job_monthly": {"type": "number"}, "job_start": {"type": "string", "description": "YYYY-MM"},
                                                       "winter_monthly": {"type": "number"}, "osap": {"type": "number"}}}),
    dict(name="can_i_afford", description="Check a one-off purchase against this month's money and the whole forecast.",
         inputSchema={"type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]}),
    dict(name="subscriptions_and_bills", description="Recurring charges detected automatically, with yearly cost and next date.",
         inputSchema={"type": "object", "properties": {}}),
    dict(name="goals", description="Savings goals and how funded they are.", inputSchema={"type": "object", "properties": {}}),
]


def _call(con, cfg, name, a):
    from . import api, planner
    from .forecast import affordability, forecast
    from .goals import goals_status
    m = a.get("month") or date.today().strftime("%Y-%m")
    if name == "money_today":
        t = api.today_page(con, cfg, m)
        return dict(headline=t["headline"], today=t["safe"]["today_allowance"], rest_of_month=t["safe"]["safe_to_spend"],
                    per_day=t["safe"]["per_day"], fixed_bills=t["safe"]["fixed_items"], upcoming=t["upcoming"][:8])
    if name == "spending_summary":
        return dict(kpis=ins.kpis(con, m), categories=ins.by_category(con, m)["categories"][:20], merchants=ins.top_merchants(con, m, 10))
    if name == "search_transactions":
        where, args = ["1=1"], []
        if a.get("month"):
            where.append("substr(date,1,7)=?"); args.append(a["month"])
        if a.get("category"):
            where.append("category=?"); args.append(a["category"])
        if a.get("query"):
            where.append("(merchant LIKE ? OR description LIKE ? OR counterparty LIKE ? OR note LIKE ?)"); args += [f"%{a['query']}%"] * 4
        lim = max(1, min(int(a.get("limit") or 50), 200))
        rows = con.execute(f"SELECT date, merchant, category, kind, amount, note FROM transactions WHERE {' AND '.join(where)} "
                           f"ORDER BY date DESC LIMIT {lim}", args).fetchall()
        return [dict(r) for r in rows]
    if name == "cash_forecast":
        sc = api.scenario_from_query({k: str(v) for k, v in a.items()})
        f = forecast(con, cfg, scenario=sc)
        return dict(start=f["start"], relative=f["relative"], low=f["low"], below_buffer=f["below_buffer"], below_zero=f["below_zero"],
                    months=f["months"], assumptions=f["assumptions"])
    if name == "can_i_afford":
        s = planner.safe_to_spend(con, cfg)
        return dict(this_month_left=s["safe_to_spend"], **affordability(con, cfg, float(a["amount"])))
    if name == "subscriptions_and_bills":
        return [r for r in ins.recurring(con) if r["active"]]
    if name == "goals":
        return goals_status(con, cfg)
    raise ValueError(f"unknown tool {name}")


def serve_stdio(cfg):
    con = db.connect(cfg["_home"] / "ledger.db")
    lock = threading.Lock()
    out = sys.stdout

    def send(obj):
        out.write(json.dumps(obj, default=str) + "\n")
        out.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        if mid is None:
            continue  # notification
        try:
            if method == "initialize":
                res = {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                       "serverInfo": {"name": "ledger", "version": "4.0"}}
            elif method == "ping":
                res = {}
            elif method == "tools/list":
                res = {"tools": TOOLS}
            elif method == "tools/call":
                with lock:
                    data = _call(con, cfg, params.get("name"), params.get("arguments") or {})
                res = {"content": [{"type": "text", "text": json.dumps(data, default=str)}]}
            else:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}})
                continue
            send({"jsonrpc": "2.0", "id": mid, "result": res})
        except Exception as e:
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}})
