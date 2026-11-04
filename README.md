# Ledger

A private money manager that runs on your own computer and keeps itself up to date. It reads the alert emails your banks already send you, so it never needs a bank login. It sorts every transaction, tells you what you can spend today, forecasts your cash months ahead, and messages you when something needs your attention.

```
 Bank / Interac / Wealthsimple alert emails ─┐  IMAP IDLE (instant)    ┌─ Dashboard (laptop + phone via Tailscale)
 Apple Pay taps (iPhone Shortcut) ───────────┤                         ├─ Phone home-screen widget
 Bank CSV/OFX saved to Downloads ────────────┼──▶ Ledger (SQLite) ─────┼─ Telegram: brief, alerts, chat
 Local AI (Ollama) for anything unrecognised ┘   on your PC            └─ Claude Desktop / Code via MCP (read-only)
```

- **No bank logins, no aggregators.** Canadian bank aggregators still need your bank password, and open banking isn't live yet. Ledger instead reads the notification emails your banks send.
- **Zero dependencies.** Python standard library only, so there's nothing from third parties to trust or keep updated. The one exception is Chart.js, which is vendored (bundled as a local copy).
- **Everything stays local.** The database, backups and logs all live in `~/Ledger/`.
- **Security details:** see [SECURITY.md](SECURITY.md).

## What it does
| | |
|---|---|
| **Intake** | Real-time Gmail via IMAP IDLE, read-only. Bank emails that fail DKIM/DMARC checks are quarantined, so fake "Tangerine" emails can't add transactions. Bank exports saved to Downloads are imported automatically. Apple Pay taps arrive through an iPhone Shortcut. Anything unrecognised is read by a local AI model. |
| **Sorting** | Hundreds of built-in Canadian merchant patterns. It also learns from your corrections and from e-transfer messages. You can set per-person rules, including by amount (e.g. "$800 from my roommate = rent paid back"). |
| **Money logic** | Transfers between your own accounts never count as spending. Reimbursements reduce spending instead of counting as income. Refunds, trips and split monthly payments are all recognised. |
| **Planning** | Safe-to-spend today and for the rest of the month. A cash forecast to your horizon that follows your life events. "What if…" scenarios. "Can I afford $X?". Auto-funded goals and learned budgets. |
| **Assistant** | Morning brief, payday breakdown, bill reminders, unusual-charge and large-purchase alerts, low-balance nudges, forecast warnings, weekly and monthly reports. |
| **Self-care** | Starts at login and after unlock, and restarts if it crashes. Nightly backups (14 kept). A rotating log file. A data-quality panel. Bank emails it couldn't read are retried automatically. |

## Setup (Windows; macOS and Linux work the same way)
```powershell
winget install Python.Python.3.12           # 3.11+
git clone https://github.com/raz3rbla8e/Ledger.git $HOME\Ledger-app
cd $HOME\Ledger-app
python -m ledger setup
```
`setup` walks you through:
1. **Your profile.** If `ledger-config.toml` is in your Downloads folder, it's installed as `~/Ledger/config.toml`. Otherwise, copy `config.example.toml` there and edit it.
2. **Gmail app passwords.** Each one is tested live, then stored in **Windows Credential Manager**, never in a plain-text file.
3. **Telegram bot**, linked automatically.
4. **Phone tokens and an optional dashboard password.**
5. **Local AI check.**
6. **Autostart** through a Task Scheduler entry that starts at login and after unlock, and restarts on failure.

Then:
```powershell
python -m ledger run          # first sync pulls history; dashboard at http://127.0.0.1:8765
python -m ledger status       # running? inbox health? last backup?
```
On the dashboard, type each account balance once (System → Accounts & balances). Ledger keeps them current from then on.

### Phone
1. Install [Tailscale](https://tailscale.com) on the laptop and the iPhone, signed in to the same account.
2. On the laptop, run `tailscale serve --bg 8765`. The dashboard is now at `https://<laptop>.<tailnet>.ts.net`, visible only to your own devices and never on the public internet.
3. **Full dashboard:** open that address in Safari, then Share → *Add to Home Screen*. It asks for the dashboard password.
4. **Widget:** install **Scriptable**, then paste [`phone/ledger-widget.js`](phone/ledger-widget.js) into a new script. Fill in your `ts.net` address and the widget token, then add a Scriptable widget to your home screen.
5. **Apple Pay → instant entries:**
   1. In Shortcuts, go to Automation, tap **+**, choose **Wallet**, pick your card, and set it to **Run Immediately**.
   2. Add the action **Get Contents of URL**:
      - URL: `https://<laptop>.<tailnet>.ts.net/api/hook/applepay`
      - Method: POST
      - Header: `X-Ledger-Token` = your Apple Pay token
      - JSON body: `amount` = *Shortcut Input → Amount*, `merchant` = *Merchant*, `card` = *Card or Pass*

### Local AI (optional)
Install [Ollama](https://ollama.com), then run `ollama pull qwen3:8b` (fits an 8 GB GPU). Its output is schema-constrained, meaning the model can only return valid JSON. Nothing leaves your PC.

### Claude Desktop / Claude Code (optional, read-only)
```json
{ "mcpServers": { "ledger": { "command": "python", "args": ["-m", "ledger", "mcp"], "cwd": "C:\\Users\\you\\Ledger-app" } } }
```
Tools: `money_today`, `spending_summary`, `search_transactions`, `cash_forecast`, `can_i_afford`, `subscriptions_and_bills`, `goals`. None of them can change anything.

## Commands
```
python -m ledger setup | run | status | sync | ask "can I afford $200?" | report | mcp | accounts | recat | autostart | demo
python -m tests.test_pipeline && python -m tests.test_v3 && python -m tests.test_v4
```

## Layout
- `ledger/ingest_email.py`: parsers, sender authentication, UID sync, IMAP IDLE
- `ingest_files.py`: CSV/OFX and the Downloads watcher
- `applepay.py`: the Apple Pay hook
- `store.py`: dedup, alert↔statement merge, transfer pairing
- `categorize.py`: the categorization engine
- `balances.py`, `forecast.py`, `planner.py`, `goals.py`: planning
- `autopilot.py`, `notify.py`, `assistant.py`: the assistant
- `server.py`, `api.py`, `static/`: the dashboard
- `widget.py`, `phone/`: the phone widget
- `mcp_server.py`: the MCP server
- `secrets_store.py`: OS keychain
- `backup.py`, `logs.py`: backups and logging
- `demo.py`, `demo_profile.py`: fictional demo data
