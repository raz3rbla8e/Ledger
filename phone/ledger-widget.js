// Ledger widget for Scriptable (iOS). Shows what you can spend today + the rest of the month.
// 1. Install Scriptable + Tailscale on your iPhone (signed in to the same Tailscale account as your laptop).
// 2. On the laptop:  tailscale serve --bg 8765      → note the https://<name>.<tailnet>.ts.net address
// 3. Paste that address and your widget token (from `python -m ledger setup`) below.
// 4. Add a Scriptable widget (small or medium) to your home screen and pick this script.
// The token only unlocks these few numbers — it can't see transactions or change anything.

const LEDGER_URL = "https://YOUR-LAPTOP.YOUR-TAILNET.ts.net";
const TOKEN = "PASTE-WIDGET-TOKEN-HERE";

const money = v => (v < 0 ? "−" : "") + "$" + Math.abs(Math.round(v)).toLocaleString("en-CA");
const tones = { good: new Color("#0ca30c"), warn: new Color("#ec835a"), bad: new Color("#d03b3b") };

async function load() {
  const req = new Request(LEDGER_URL + "/api/widget");
  req.headers = { "X-Ledger-Token": TOKEN };
  req.timeoutInterval = 10;
  const data = await req.loadJSON();
  Keychain.set("ledger-widget-cache", JSON.stringify(data));   // last good numbers, for when the laptop is asleep
  return { data, stale: false };
}

let res;
try { res = await load(); }
catch (e) {
  const c = Keychain.contains("ledger-widget-cache") ? JSON.parse(Keychain.get("ledger-widget-cache")) : null;
  res = { data: c, stale: true };
}

const w = new ListWidget();
w.backgroundColor = Color.dynamic(new Color("#fcfcfb"), new Color("#1a1a19"));
w.url = LEDGER_URL;
const fg = Color.dynamic(new Color("#0b0b0b"), new Color("#f0efec"));
const muted = Color.dynamic(new Color("#86857f"), new Color("#8f8e86"));

if (!res.data) {
  const t = w.addText("Ledger unreachable"); t.textColor = muted; t.font = Font.mediumSystemFont(13);
} else {
  const d = res.data;
  const top = w.addStack(); top.centerAlignContent();
  const dot = top.addText("●"); dot.textColor = tones[d.tone] || muted; dot.font = Font.systemFont(10);
  top.addSpacer(4);
  const lbl = top.addText("Today you can spend"); lbl.textColor = muted; lbl.font = Font.mediumSystemFont(11);
  w.addSpacer(2);
  const big = w.addText(money(d.today)); big.textColor = fg; big.font = Font.boldSystemFont(34); big.minimumScaleFactor = 0.6;
  const sub = w.addText(`${money(d.left_month)} left · ${d.days_left} days`); sub.textColor = muted; sub.font = Font.systemFont(12);
  if (config.widgetFamily !== "small" && d.next_bill) {
    w.addSpacer(6);
    const nb = w.addText(`Next: ${d.next_bill.name} ${money(d.next_bill.amount)} · ${d.next_bill.date}`);
    nb.textColor = muted; nb.font = Font.systemFont(11);
  }
  if (res.stale) { w.addSpacer(4); const s = w.addText("offline · last known"); s.textColor = muted; s.font = Font.italicSystemFont(9); }
}
w.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
Script.setWidget(w);
if (config.runsInApp) await w.presentMedium();
Script.complete();
