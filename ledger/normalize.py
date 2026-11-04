"""Turn raw bank descriptions into clean merchant / person names."""
import html
import re

PROCESSOR_PREFIXES = ("SQ", "TST", "PAYPAL", "PP", "SP", "DD", "IC", "PY", "SUMUP", "ZTL", "CKO", "LS", "GOOGLE", "APPLE.COM/BILL")
PRETTY = {
    "AMZN": "Amazon", "AMAZON": "Amazon", "AMZ": "Amazon", "AMAZON.CA": "Amazon", "AMZN MKTP": "Amazon",
    "ANTHROPIC": "Anthropic (Claude)", "OPENAI": "OpenAI", "CHATGPT": "OpenAI",
    "UBER EATS": "Uber Eats", "UBEREATS": "Uber Eats", "UBER": "Uber", "UBER TRIP": "Uber",
    "DOORDASH": "DoorDash", "SKIPTHEDISHES": "SkipTheDishes", "SKIP THE DISHES": "SkipTheDishes",
    "TIM HORTONS": "Tim Hortons", "STARBUCKS": "Starbucks", "MCDONALD'S": "McDonald's", "MCDONALDS": "McDonald's",
    "COSTCO GAS": "Costco Gas", "MB-CREDIT CARD/LOC PAY.": "Credit Card Payment", "MB-CREDIT CARD/LOC PAY": "Credit Card Payment", "COSTCO WHOLESALE": "Costco", "COSTCO": "Costco",
    "LOBLAWS": "Loblaws", "REAL CDN SUPERSTORE": "Real Canadian Superstore", "NOFRILLS": "No Frills", "NO FRILLS": "No Frills",
    "SHOPPERS DRUG MART": "Shoppers Drug Mart", "SDM": "Shoppers Drug Mart",
    "NETFLIX.COM": "Netflix", "NETFLIX": "Netflix", "SPOTIFY": "Spotify", "YOUTUBEPREMIUM": "YouTube Premium",
    "PRESTO": "Presto", "OC TRANSPO": "OC Transpo", "LCBO": "LCBO", "THE BEER STORE": "The Beer Store",
    "WALMART": "Walmart", "WAL-MART": "Walmart", "CANADIAN TIRE": "Canadian Tire", "BEST BUY": "Best Buy",
    "GOODLIFE": "GoodLife Fitness", "PETRO-CANADA": "Petro-Canada", "ESSO": "Esso", "SHELL": "Shell",
}
PROVINCES = r"(ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU|CA|US|USA|CAN)"
CITY_TAIL = re.compile(r"\s+(OTTAWA|TORONTO|NEPEAN|KANATA|GATINEAU|CAMBRIDGE|WATERLOO|KITCHENER|MONTREAL|VANCOUVER|ORLEANS|MISSISSAUGA|DEARBORN|SAN FRANCISCO|INTERNET|WWW\.\S+|HTTPS?\S*)?\s*" + PROVINCES + r"?\s*$")


def clean_text(s: str) -> str:
    s = html.unescape(s or "")
    return re.sub(r"\s+", " ", s).strip()


def _titlecase(s: str) -> str:
    out = []
    for w in s.split(" "):
        if not w:
            continue
        if len(w) <= 3 and w.isalpha() and w.upper() in {"LCBO", "AMC", "IGA", "KFC", "A&W", "CN", "UPS", "BMO", "TD", "RBC", "NBC"}:
            out.append(w.upper())
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def merchant_from_description(desc: str) -> str:
    s = clean_text(desc).upper()
    if not s:
        return ""
    # strip common bank prefixes
    s = re.sub(r"^(POS|PURCHASE|DEBIT|VISA DEBIT|INTERAC PURCHASE|IDP PURCHASE|CONTACTLESS INTERAC PURCHASE|PRE-AUTHORIZED|PREAUTHORIZED|PAP|BILL PAYMENT|ONLINE PURCHASE|ACHAT)\s*[-:]?\s*", "", s)
    s = re.sub(r"\s+-\s+\d{3,}$", "", s)
    s = re.sub(r"^(DIRECT DEPOSIT FROM|DEPOSIT FROM|PAYMENT (TO|FROM)|BILL PAYMENT TO)\s+", "", s)
    if "*" in s:
        head, tail = s.split("*", 1)
        head, tail = head.strip(), tail.strip()
        if (head in PROCESSOR_PREFIXES or len(head) <= 3) and tail:
            s = tail
        elif tail and not re.search(r"\d", tail) and len(tail.split()) <= 2:
            s = f"{head} {tail}"
        else:
            s = head
    # trailing reference junk: tokens containing digits (store #, auth codes) and phone numbers
    toks = s.split(" ")
    while len(toks) > 1 and (re.search(r"\d", toks[-1]) or toks[-1] in {"#", "-", "INC", "INC.", "LTD", "LTD.", "CORP"}):
        toks.pop()
    s = " ".join(toks)
    s = CITY_TAIL.sub("", s).strip(" -#.,") or s
    s = re.sub(r"\s+#?\d{2,}\b.*$", "", s).strip() or s
    for key in sorted(PRETTY, key=len, reverse=True):
        if s == key or s.startswith(key + " ") or s.startswith(key + "."):
            return PRETTY[key]
    return _titlecase(s)


ETRANSFER = re.compile(
    r"(?:E-?\s?TRANSFER|E-?TFR|EMAIL (?:MONEY )?TRANSFER|\bEMT\b|VIREMENT INTERAC|VIR(?:EMENT)? COURRIEL)\b.*?\b(?:FROM|TO|SENT|RECEIVED|AUTODEPOSIT|DE|À)\b\s*[:\-]?\s*([A-Z][A-Z .'\-]{2,})",
    re.I,
)


def counterparty_from_description(desc: str):
    m = ETRANSFER.search(clean_text(desc))
    if not m:
        return None
    name = re.sub(r"\s+(CA|REF\b.*|\d.*)$", "", m.group(1).strip(), flags=re.I).strip(" -.")
    return name.upper() if len(name) >= 3 else None


def person_display(name: str) -> str:
    return _titlecase(clean_text(name).upper())


def institution_from_text(s: str):
    s = (s or "").lower()
    for key, inst in [("tangerine", "Tangerine"), ("rbc", "RBC"), ("royal bank", "RBC"), ("national bank", "National Bank"),
                      ("bnc", "National Bank"), ("nbc", "National Bank"), ("wealthsimple", "Wealthsimple"),
                      ("td ", "TD"), ("scotia", "Scotiabank"), ("cibc", "CIBC"), ("bmo", "BMO"), ("simplii", "Simplii"), ("eq bank", "EQ Bank")]:
        if key in s:
            return inst
    return None


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
