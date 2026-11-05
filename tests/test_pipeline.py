"""End-to-end checks with real-format bank emails and CSV exports. Run: python -m tests.test_pipeline"""
import os, re, sys, tempfile, json, urllib.request, threading, time
from pathlib import Path
from email.message import EmailMessage
tmp = tempfile.mkdtemp(); os.environ["LEDGER_HOME"] = tmp; os.environ["LEDGER_NO_WATCH"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ledger import config, db
from ledger.categorize import Categorizer
from ledger.ingest_email import ingest_message, parse_email
from ledger.sync import run_sync

from ledger import demo_profile  # noqa: E402
cfg = demo_profile.apply(config.load()); con = db.connect(Path(tmp) / "ledger.db"); cz = Categorizer(con, cfg)

def mail(sender, subj, body, date="Fri, 23 Oct 2026 10:26:56 -0400", html=False, auth=True):
    m = EmailMessage(); m["From"] = sender; m["Subject"] = subj; m["Date"] = date; m["Message-ID"] = f"<{abs(hash(subj+body+date+str(auth)))}@t>"
    dom = re.search(r"@([\w.\-]+)", sender)
    if dom and auth:
        m["Authentication-Results"] = f"mx.google.com; dkim=pass header.i=@{dom.group(1)}; dmarc=pass (p=REJECT) header.from={dom.group(1)}"

    if html: m.set_content("x"); m.add_alternative(body, subtype="html")
    else: m.set_content(body)
    return m.as_bytes()

TANG = "Tangerine\n\n| |\n| Hi Jordan Avery, Just a quick note to let you know that a transaction of ${a} was made on your Tangerine Money-Back Credit Card 5360 xxxx xxxx 9999 at {m} on October {d}, 2026. If you have any questions, please don't hesitate to call us at 1-888-826-4374. Sincerely, Tangerine Forward Banking |\n"
emails = [
  mail("donotreply@tangerine.ca", "A new Credit Card transaction has been made", TANG.format(a="15.81", m="Spotify P48582C214", d=23)),
  mail("donotreply@tangerine.ca", "A new Credit Card transaction has been made", TANG.format(a="31.64", m="ANTHROPIC* CLAUDE SUB", d=23)),
  mail("donotreply@tangerine.ca", "A new Credit Card transaction has been made", TANG.format(a="83.28", m="COSTCO GAS W1263", d=23)),
  mail("donotreply@tangerine.ca", "A new Credit Card transaction has been made", "<table><tr><td>Hi Jordan Avery, Just a quick note to let you know that a transaction of $14.68 was made on your Tangerine Money-Back Credit Card 5360 xxxx xxxx 9999 at MELT &amp; GRILL on October 24, 2026. If you</td></tr></table>", html=True),
  mail("donotreply@tangerine.ca", "Your Credit Card payment has been received", "Hi Jordan Avery, Just a quick note to let you know that we've received your Tangerine Money-Back Credit Card payment of $76.29. If you have", "Mon, 02 Nov 2026 01:35:40 +0000"),
  mail("donotreply@tangerine.ca", "Your Money-Back Rewards have been deposited", "Hi Jordan Avery, Just a quick note to let you know that we've deposited your Money-Back Rewards of $16.88 into your Tangerine Money-Back Credit Card. If you have"),
  mail("notify@payments.interac.ca", "Interac e-Transfer: You've received $350.00 from SAM ROOMIE and it has been automatically deposited.",
       "Hi Jordan Avery Example,\n\nFunds Deposited!\n$350.00\n\nYour funds have been automatically deposited into your account at Tangerine Bank.\n\nTangerine Bank\nAccount ending in 4321\n\nTransfer Details\n\nDate: Nov 1, 2026\nReference Number: C1AGPkGS8jta\nSent From: SAM ROOMIE\nAmount: $350.00 (CAD)\nMessage: hydro bill\n\nThis email was sent to you by Interac Corp., the owner of the Interac e-Transfer service, on behalf of SAM ROOMIE at RBC Royal Bank."),
  mail("catch@payments.interac.ca", "Interac e-Transfer: Your $40.00 transfer to Jordan Avery Example has been successfully deposited.",
       "Hi Jordan Avery Example,\n\nYour transfer to Jordan Avery Example was successfully deposited.\n\nThe $40.00 (CAD) you sent to Jordan Avery Example has been successfully deposited.\n\nTransfer Details\n\nDate: Nov 2, 2026\nReference Number: CAK78MhA\n\n2000 - 2026 Interac Corp.\n\nThis email was sent to you by Interac Corp., the owner of the Interac e-Transfer service, on behalf of Tangerine Bank"),
  mail("catch@payments.interac.ca", "Interac e-Transfer: Your $10.00 transfer to CASEY FRIEND has been successfully deposited.",
       "Hi Jordan,\n\nThe $10.00 (CAD) you sent to CASEY FRIEND has been successfully deposited.\n\nDate: Oct 30, 2026\nReference Number: X1\n\nThis email was sent to you by Interac Corp., the owner of the Interac e-Transfer service, on behalf of Tangerine Bank"),
  mail("donotreply@tangerine.ca", "Orange Alert: Low Account Balance", "Your balance is low."),
]
st = {}
for e in emails: ingest_message(con, cfg, cz, e, st)
for e in emails: ingest_message(con, cfg, cz, e, st)  # re-run must not duplicate
con.commit()
rows = [dict(r) for r in con.execute("SELECT account_id,date,amount,merchant,counterparty,category,kind,memo FROM transactions ORDER BY id")]
for r in rows: print(r)
assert len(rows) == 9, len(rows)
by = {r["merchant"]: r for r in rows}
assert by["Spotify"]["category"] == "Streaming & Music" and by["Spotify"]["amount"] == -15.81 and by["Spotify"]["account_id"] == "tangerine-cc-9999"
assert by["Anthropic (Claude)"]["category"] == "Software & AI"
assert by["Costco Gas"]["category"] == "Gas"
assert by["Melt & Grill"]["category"] == "Restaurants", by["Melt & Grill"]
assert by["Credit Card Payment Received"]["kind"] == "transfer" and by["Credit Card Payment Received"]["account_id"] == "tangerine-cc-9999"
assert by["Money-back Rewards"]["category"] == "Cashback & Rewards" and by["Money-back Rewards"]["kind"] == "income"
assert by["Sam Roomie"]["account_id"] == "tangerine-4321" and by["Sam Roomie"]["memo"] == "hydro bill" and by["Sam Roomie"]["category"] == "Utilities" and by["Sam Roomie"]["kind"] == "reimbursement"
assert by["Jordan Avery Example"]["kind"] == "transfer" and by["Jordan Avery Example"]["account_id"] == "tangerine-4321"
assert by["Casey Friend"]["category"] == "Paid to People"
print("emails OK", st)

# statements: Tangerine chequing CSV (merges with e-transfer alerts), RBC CSV, NBC semicolon CSV, Tangerine CC (positive purchases), OFX
inbox = Path(tmp) / "inbox"
for d in ["tangerine-4321", "rbc-chequing", "nbc-chequing", "tangerine-cc-9999", "wealthsimple-cash"]: (inbox / d).mkdir(parents=True, exist_ok=True)
(inbox / "tangerine-4321" / "t.csv").write_text("Date,Transaction,Name,Memo,Amount\n10/31/2026,DEBIT,INTERAC e-Transfer To: CASEY FRIEND,,-10.00\n11/01/2026,CREDIT,INTERAC e-Transfer From: SAM ROOMIE,,350.00\n11/02/2026,DEBIT,MB-CREDIT CARD/LOC PAY.,To 5360,-76.29\n11/02/2026,DEBIT,INTERAC e-Transfer To: JORDAN AVERY EXAMPLE,,-40.00\n10/28/2026,DEBIT,LOBLAWS #1032,Rewards earned: 0,-64.12\n")
(inbox / "rbc-chequing" / "csv79.csv").write_text('"Account Type","Account Number","Transaction Date","Cheque Number","Description 1","Description 2","CAD$","USD$"\nChequing,01234-5678,11/2/2026,,"e-Transfer - Autodeposit","JORDAN AVERY EXAMPLE",40.00,\nChequing,01234-5678,10/15/2026,,"Deposit","CANADA GST",129.00,\nChequing,01234-5678,10/20/2026,,"Monthly fee","",-4.00,\n')
(inbox / "nbc-chequing" / "export.csv").write_text("Date;Description;Category;Debit;Credit;Balance\n2026-10-03;PRESTO FARE/ OTTAWA;Transport;3.50;;100.00\n2026-10-04;LCBO #123;;24.95;;75.05\n")
(inbox / "tangerine-cc-9999" / "cc.csv").write_text("Transaction date,Transaction,Name,Memo,Amount\n10/23/2026,DEBIT,Spotify P48582C214,,15.81\n10/24/2026,DEBIT,MELT & GRILL,,14.68\n10/25/2026,DEBIT,UBER* EATS,,22.40\n10/26/2026,CREDIT,PAYMENT - THANK YOU,,-200.00\n")
(inbox / "wealthsimple-cash" / "x.ofx").write_text("OFXHEADER:100\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><ACCTID>WS123</BANKACCTFROM><BANKTRANLIST>\n<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261030120000<TRNAMT>2186.42<FITID>a1<NAME>FORD MOTOR CO CANADA PAYROLL</STMTTRN>\n<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261031<TRNAMT>-500.00<FITID>a2<NAME>Wealthsimple Invest TFSA</STMTTRN>\n</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>")
cfg["email"]["enabled"] = False
print(run_sync(con, cfg))
rows = [dict(r) for r in con.execute("SELECT id,account_id,date,amount,merchant,category,kind,status,link_id FROM transactions ORDER BY account_id,date")]
for r in rows: print(r)
g = lambda acct, amt: [r for r in rows if r["account_id"] == acct and abs(r["amount"] - amt) < .01]
assert len(g("tangerine-4321", 350)) == 1 and g("tangerine-4321", 350)[0]["status"] == "posted"      # merged, not duplicated
assert len(g("tangerine-4321", -10)) == 1
assert len(g("tangerine-cc-9999", -15.81)) == 1 and len(g("tangerine-cc-9999", -22.40)) == 1           # CC sign auto-flipped + merged
assert g("tangerine-cc-9999", -22.40)[0]["category"] == "Food Delivery"
assert g("tangerine-4321", -76.29)[0]["link_id"] == g("tangerine-cc-9999", 76.29)[0]["id"]             # cc payment pair
assert g("tangerine-4321", -40)[0]["link_id"] == g("rbc-chequing", 40)[0]["id"]                         # self e-transfer pair across banks
assert g("rbc-chequing", 129)[0]["category"] == "Government & Benefits"
assert g("rbc-chequing", -4)[0]["category"] == "Bank Fees"
assert g("nbc-chequing", -3.5)[0]["category"] == "Transit" and g("nbc-chequing", -24.95)[0]["category"] == "Alcohol"
assert g("wealthsimple-cash", 2186.42)[0]["category"] == "Salary" and g("wealthsimple-cash", -500)[0]["category"] == "Investing"
print(run_sync(con, cfg))  # idempotent
assert con.execute("select count(*) from transactions").fetchone()[0] == len(rows)
print("statements OK")

# dashboard API: teach a merchant once → applies to all
from ledger.server import make_handler
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(con, cfg)); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
def call(p, body=None, method=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{p}", data=json.dumps(body).encode() if body is not None else None, method=method, headers={"content-type": "application/json", "X-Ledger-Req": "1"})
    return json.load(urllib.request.urlopen(req))
cid = g("tangerine-4321", -10)[0]["id"]
print(call(f"/api/tx/{cid}", {"category": "Gifts", "apply_all": True}))
ov = call("/api/overview?month=2026-10"); assert ov["kpis"]["income"] > 2000, ov["kpis"]
print("kpis", ov["kpis"]); print("groups", ov["categories"]["groups"])
assert call("/api/meta")["rules"][0]["pattern"] == "CASEY FRIEND"
# new e-transfer to the same person → auto Gifts
ingest_message(con, cfg, Categorizer(con, cfg), mail("catch@payments.interac.ca", "Interac e-Transfer: Your $25.00 transfer to CASEY FRIEND has been successfully deposited.", "The $25.00 (CAD) you sent\nDate: Nov 5, 2026\n on behalf of Tangerine Bank"), {})
assert con.execute("select category from transactions where amount=-25").fetchone()[0] == "Gifts"
print("API + learning OK")

# spoofed bank email (fails DKIM/DMARC) must NOT become a transaction
fake = mail("donotreply@tangerine.ca", "A new Credit Card transaction has been made", TANG.format(a="999.99", m="EVIL STORE", d=25), auth=False)
st2 = {}
ingest_message(con, cfg, Categorizer(con, cfg), fake, st2)
assert st2.get("quarantined") == 1 and not con.execute("select 1 from transactions where amount=-999.99").fetchone()
print("spoof blocked OK")

# ── Wealthsimple inbox (real formats, from the 2nd Gmail) ──
WS_FOOT = "| Email or text messages carry the notice while the financial institutions securely transfer the money using existing payment networks. This email was sent to you by Interac Corp., the owner of the Interac e-Transfer service, on behalf of Wealthsimple |\n| |"
ws = [
  mail("Wealthsimple <notifications@o.wealthsimple.com>", "Direct deposit received",
       "| |\n| # We got your direct deposit! |\n\n| |\n| Amount: $992.89 From: FORD MOTOR COMP Your deposit is now available in your chequing account. |\n", "Wed, 28 Oct 2026 17:01:34 -0400"),
  mail("Wealthsimple <catch@payments.interac.ca>", "Interac e-Transfer: Your $1,850.00 transfer to PAT LANDLORD has been successfully deposited.",
       "| # Hi Jordan Example, |\n| ## Your transfer to PAT LANDLORD was successfully deposited. |\n| The $1,850.00 (CAD) you sent to PAT LANDLORD has been successfully deposited. |\n| Transfer Details |\n| Date: |\n| |\n\nOct 30, 2026\n\n| | Reference Number: |\n| |\n\nC1AnBKDbyBNc\n\n" + WS_FOOT, "Fri, 30 Oct 2026 11:26:14 -0400"),
  mail("Wealthsimple <catch@payments.interac.ca>", "Interac e-Transfer: Dad has accepted your transfer of $750.00",
       "| Jordan Example, your transfer of $750.00 (CAD) has been deposited by Dad |\n| ## Dad has accepted your transfer of |\n| ### $750.00 |\n| Date: |\n| |\n\nOct 30, 2026\n\n| | Reference Number: |\n| |\n\nCATNddET\n\n" + WS_FOOT.replace("Wealthsimple |", "Wealthsimple. |"), "Fri, 30 Oct 2026 20:07:39 -0400"),
  mail("Wealthsimple <catch@payments.interac.ca>", "Interac e-Transfer: Your $685.00 transfer to Jordan Avery Example has been successfully deposited.",
       "| The $685.00 (CAD) you sent to Jordan Avery Example has been successfully deposited. |\n| Date: |\n| |\n\nOct 29, 2026\n\n| | Reference Number: |\n| |\n\nC1AJV8WEd97u\n\n" + WS_FOOT, "Thu, 29 Oct 2026 09:31:30 -0400"),
  mail("notify@payments.interac.ca", "Interac e-Transfer: You've received $685.00 from Jordan Example and it has been automatically deposited.",
       "Hi Jordan Avery Example,\n\nFunds Deposited!\n$685.00\n\nYour funds have been automatically deposited into your account at Tangerine Bank.\n\nTangerine Bank\nAccount ending in 4321\n\nTransfer Details\n\nDate: Oct 29, 2026\nReference Number: C1AJV8WEd97u\nSent From: Jordan Example\nAmount: $685.00 (CAD)\n\nThis email was sent to you by Interac Corp., the owner of the Interac e-Transfer service, on behalf of Jordan Example at Wealthsimple.", "Thu, 29 Oct 2026 09:31:30 -0400"),
  mail("notify@payments.interac.ca", "Interac e-Transfer: You've received $129.00 from Jordan Example and it has been automatically deposited.",
       "Funds Deposited!\n\nYour funds have been automatically deposited into your account at Tangerine Bank.\n\nTangerine Bank\nAccount ending in 4321\n\nDate: Oct 9, 2026\nReference Number: C9XYZabc\n\nThis email was sent to you by Interac Corp., the owner of the Interac e-Transfer service, on behalf of Jordan Example at RBC Royal Bank.", "Fri, 09 Oct 2026 09:31:30 -0400"),
]
cz = Categorizer(con, cfg)
for e in ws: ingest_message(con, cfg, cz, e, {})
from ledger.store import pair_transfers; pair_transfers(con); con.commit()
q = lambda sql: [dict(r) for r in con.execute(sql)]
pay = q("select * from transactions where amount=992.89")[0]
assert pay["account_id"].startswith("wealthsimple") and pay["category"] == "Salary" and pay["kind"] == "income", pay
rent = q("select * from transactions where amount=-1850")[0]
assert rent["account_id"].startswith("wealthsimple") and rent["category"] == "Rent" and rent["date"] == "2026-10-30", rent
dad = q("select * from transactions where amount=-750")[0]
assert dad["category"] == "Family Support" and dad["counterparty"] == "DAD", dad
legs = q("select * from transactions where abs(amount)=685 and kind='transfer'")
assert len(legs) == 2 and legs[0]["link_id"] == legs[1]["id"], legs
gov = q("select * from transactions where amount=129 and account_id='tangerine-4321'")[0]
assert gov["category"] == "Government & Benefits" and gov["kind"] == "income", gov
print("Wealthsimple OK")

# assistant
from ledger import planner
from ledger.assistant import builtin
print(planner.safe_to_spend(con, cfg))
print(builtin(con, cfg, "how much on rent in october"))
assert "1,850" in builtin(con, cfg, "how much on rent in october")
print(builtin(con, cfg, "/safe"))
print(builtin(con, cfg, "can I afford $200?")[:120])
from ledger.notify import weekly_report, detect
print(weekly_report(con, cfg)[:300])
detect(con, cfg); print("ALL TESTS PASSED")
