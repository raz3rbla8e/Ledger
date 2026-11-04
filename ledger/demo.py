"""Fake-but-realistic year of data, pushed through the REAL pipeline (synthetic bank emails + CSV files),
so the demo exercises exactly the code your real data will."""
import random
from datetime import date, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime

from .categorize import Categorizer
from .ingest_email import ingest_message
from .ingest_files import sync_files
from .store import pair_transfers

CC_LAST4, CHQ_LAST4 = "4242", "1111"
ME = "Jordan Avery Example"


def _mail(sender, subject, body, when):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, "me@example.com", subject
    m["Date"] = format_datetime(datetime.combine(when, datetime.min.time()).replace(hour=random.randint(8, 21)).astimezone())
    m["Message-ID"] = f"<demo-{random.getrandbits(64):x}@demo>"
    m.set_content(body)
    return m.as_bytes()


def cc_purchase(when, amt, merchant):
    return _mail("Tangerine <donotreply@tangerine.ca>", "A new Credit Card transaction has been made",
                 f"Hi Jordan Avery, Just a quick note to let you know that a transaction of ${amt:,.2f} was made on your "
                 f"Tangerine Money-Back Credit Card 5360 xxxx xxxx {CC_LAST4} at {merchant} on {when:%B} {when.day}, {when.year}. "
                 "If you have any questions, please don't hesitate to call us.", when)


def cc_payment(when, amt):
    return _mail("Tangerine <donotreply@tangerine.ca>", "Your Credit Card payment has been received",
                 f"Hi Jordan Avery, Just a quick note to let you know that we've received your Tangerine Money-Back Credit Card payment of ${amt:,.2f}.", when)


def etr_in(when, amt, who, memo=None):
    return _mail("Interac <notify@payments.interac.ca>",
                 f"Interac e-Transfer: You've received ${amt:,.2f} from {who} and it has been automatically deposited.",
                 f"Hi {ME},\n\nFunds Deposited!\n${amt:,.2f}\n\nYour funds have been automatically deposited into your account at Tangerine Bank.\n\n"
                 f"Tangerine Bank\nAccount ending in {CHQ_LAST4}\n\nTransfer Details\n\nDate: {when:%b} {when.day}, {when.year}\n"
                 f"Reference Number: D{random.getrandbits(32):x}\nSent From: {who}\nAmount: ${amt:,.2f} (CAD)\n"
                 + (f"Message: {memo}\n" if memo else "") + f"\nThis email was sent to you by Interac Corp. on behalf of {who} at RBC Royal Bank.", when)


def etr_out(when, amt, who):
    return _mail("Interac <catch@payments.interac.ca>",
                 f"Interac e-Transfer: Your ${amt:,.2f} transfer to {who} has been successfully deposited.",
                 f"Hi {ME},\n\nYour transfer to {who} was successfully deposited.\n\nThe ${amt:,.2f} (CAD) you sent to {who} has been successfully deposited.\n\n"
                 f"Transfer Details\n\nDate: {when:%b} {when.day}, {when.year}\nReference Number: C{random.getrandbits(32):x}\n\n"
                 "This email was sent to you by Interac Corp., the owner of the Interac e-Transfer service, on behalf of Tangerine Bank", when)


def ws_deposit(when, amt, payer="FORD MOTOR COMP"):
    return _mail("Wealthsimple <notifications@o.wealthsimple.com>", "Direct deposit received",
                 f"| # We got your direct deposit! |\n| Amount: ${amt:,.2f} From: {payer} Your deposit is now available in your chequing account. |", when)


def ws_sent(when, amt, who, accepted=False):
    subj = f"Interac e-Transfer: {who} has accepted your transfer of ${amt:,.2f}" if accepted else \
        f"Interac e-Transfer: Your ${amt:,.2f} transfer to {who} has been successfully deposited."
    return _mail("Wealthsimple <catch@payments.interac.ca>", subj,
                 f"| # Hi Jordan Example, |\n| The ${amt:,.2f} (CAD) you sent to {who} has been successfully deposited. |\n| Transfer Details |\n| Date: |\n| |\n\n"
                 f"{when:%b} {when.day}, {when.year}\n\n| | Reference Number: |\n| |\n\nC{random.getrandbits(32):x}\n\n"
                 "| Email or text messages carry the notice. This email was sent to you by Interac Corp., the owner of the Interac e-Transfer service, on behalf of Wealthsimple |\n| |", when)


def etr_in_from(when, amt, who, bank):
    return _mail("Interac <notify@payments.interac.ca>",
                 f"Interac e-Transfer: You've received ${amt:,.2f} from {who} and it has been automatically deposited.",
                 f"Hi {ME},\n\nFunds Deposited!\n${amt:,.2f}\n\nYour funds have been automatically deposited into your account at Tangerine Bank.\n\n"
                 f"Tangerine Bank\nAccount ending in {CHQ_LAST4}\n\nTransfer Details\n\nDate: {when:%b} {when.day}, {when.year}\n"
                 f"Reference Number: D{random.getrandbits(32):x}\nSent From: {who}\nAmount: ${amt:,.2f} (CAD)\n\n"
                 f"This email was sent to you by Interac Corp., the owner of the Interac e-Transfer service, on behalf of {who} at {bank}.", when)


def build(con, cfg, seed=7):
    from . import demo_profile
    demo_profile.apply(cfg)
    random.seed(seed)
    if con.execute("SELECT count(*) FROM transactions").fetchone()[0]:
        return
    con.commit()
    cz = Categorizer(con, cfg)
    today = date.today()
    start = date(today.year - 1, today.month, 1)
    mails, ws_rows, chq_rows = [], [], []

    d = start
    cc_balance = 0.0
    while d <= today:
        r = random.random
        def buy(m, lo, hi, p=1.0):
            nonlocal cc_balance
            if r() < p:
                a = round(random.uniform(lo, hi), 2)
                cc_balance += a
                mails.append(cc_purchase(d, a, m))
        if d.day == 3: buy("Spotify P48582C214", 15.81, 15.81)
        if d.day == 23: buy("ANTHROPIC* CLAUDE SUB", 31.64, 31.64)
        if d.day == 12: buy("NETFLIX.COM", 20.99 if d < today - timedelta(days=90) else 22.99, 20.99 if d < today - timedelta(days=90) else 22.99)
        if d.day == 1: buy("GOODLIFE FITNESS CLUBS", 49.71, 49.71)
        if d.day == 18: buy("FIDO MOBILE", 45.20, 45.20)
        if d.weekday() == 5: buy(random.choice(["LOBLAWS #1032", "FARM BOY #27", "COSTCO WHOLESALE W548", "METRO 342"]), 45, 140)
        if d.day % 9 == 0: buy("COSTCO GAS W1263", 55, 82)
        buy(random.choice(["TIM HORTONS #2231", "STARBUCKS 04312", "BRIDGEHEAD 12"]), 2.5, 9, 0.35)
        buy(random.choice(["UBER* EATS", "DOORDASH*SHAWARMA", "SKIPTHEDISHES"]), 18, 42, 0.12)
        buy(random.choice(["MELT & GRILL", "SHAWARMA PALACE", "PHO BO GA LA", "OSMOWS", "THE KEG STEAKHOUSE"]), 14, 75, 0.15)
        buy(random.choice(["AMZN Mktp CA*2K1", "AMAZON.CA*RT5"]), 15, 120, 0.06)
        buy("SHOPPERS DRUG MART #1", 8, 45, 0.05)
        buy(random.choice(["UNIQLO RIDEAU", "SPORT CHEK #432", "SSENSE MONTREAL"]), 40, 180, 0.025)
        buy(random.choice(["ZEPHYR LOUNGE", "KORI'S EMPORIUM", "PIXELWORKS STUDIO"]), 20, 90, 0.015)
        buy("HISTORY BARBER", 32, 32, 0.035)
        buy("PRESTO FARE", 3.5, 3.5, 0.08)
        if d == today - timedelta(days=6): buy("BEST BUY #941", 649.99, 649.99)

        if d.day == 1:
            mails.append(ws_sent(d, 1850.00, "PAT LANDLORD"))
        if d.day == 3:
            mails.append(etr_in(d, 800.00, "SAM ROOMIE", "rent"))
        if d.day == 30 or (d.month == 2 and d.day == 28):
            mails.append(ws_sent(d, 750.00, "Dad", accepted=True))
        if d.month in (1, 4, 7, 10) and d.day == 8:
            mails.append(etr_in_from(d, 129.00, "JORDAN EXAMPLE", "RBC Royal Bank"))
        if r() < 0.04:
            a = random.choice([10, 20, 25, 40])
            mails.append(etr_out(d, a, random.choice(["JORDAN LEE", "PRIYA NAIR", "SAM OKAFOR"])))
        if d.day == 20 and cc_balance > 0:
            pay = round(cc_balance, 2)
            mails.append(cc_payment(d, pay))
            chq_rows.append((d, -pay, "MB-CREDIT CARD/LOC PAY."))
            cc_balance = 0
        # biweekly payroll into Wealthsimple, then move spending money to Tangerine
        if (d - start).days % 14 == 4:
            mails.append(ws_deposit(d, 2186.42))
            amt = random.choice([600.0, 685.0, 700.0])
            mails.append(ws_sent(d + timedelta(days=1), amt, "Jordan Avery Example"))
            mails.append(etr_in_from(d + timedelta(days=1), amt, "JORDAN EXAMPLE", "Wealthsimple"))
            chq_rows.append((d + timedelta(days=1), amt, f"INTERAC e-Transfer From: JORDAN EXAMPLE"))
        d += timedelta(days=1)

    stats = {}
    for m in mails:
        ingest_message(con, cfg, cz, m, stats, verify=False)
    con.commit()
    # statements: chequing only for months older than this one (like real life — you export last month's)
    inbox = cfg["_inbox"]
    (inbox / f"tangerine-{CHQ_LAST4}").mkdir(parents=True, exist_ok=True)
    cutoff = date(today.year, today.month, 1)
    with open(inbox / f"tangerine-{CHQ_LAST4}" / "chequing.csv", "w") as f:
        f.write("Date,Transaction,Name,Memo,Amount\n")
        for dd, a, desc in chq_rows:
            if dd < cutoff:
                f.write(f"{dd:%m/%d/%Y},{'DEBIT' if a < 0 else 'CREDIT'},\"{desc}\",,{a:.2f}\n")
    con.execute("UPDATE accounts SET name='Tangerine Money-Back CC' WHERE id=?", (f"tangerine-cc-{CC_LAST4}",))
    sync_files(con, cfg, Categorizer(con, cfg), log=lambda *_: None)
    pair_transfers(con)
    # one-time balances, like you'd type on the System page
    from .balances import set_balance
    for a in con.execute("SELECT id, type FROM accounts").fetchall():
        set_balance(con, a["id"], -312.40 if a["type"] == "credit" else 6850.00 if a["id"].startswith("wealthsimple") else 140.00)
    from .autopilot import run as autopilot
    from .notify import detect
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('notified_upto', ?)", (str(con.execute("SELECT max(id)-12 FROM transactions").fetchone()[0]),))
    autopilot(con, cfg, log=lambda *_: None)
    detect(con, cfg)
    con.execute("INSERT INTO sync_log(summary) VALUES ('{\"demo\": true}')")
    con.commit()
    print(stats)
