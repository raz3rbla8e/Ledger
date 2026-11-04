"""Categorization engine.

Priority (first hit wins):
  1. manual           — you set it on this transaction; never overwritten
  2. self / people    — your own names → internal transfer; [[people]] config
  3. your rules       — created when you tick "apply to every <merchant>" (or by the AI fallback)
  4. learned          — you've hand-categorized this merchant before → reuse it
  5. built-in rules   — ~300 Canadian merchants/patterns below
  6. e-transfer fallback, refunds, AI fallback, then Review queue
"""
import re

from .config import my_names

IN, OUT = "in", "out"

# (regex, category, direction or None). Matched against "RAW DESCRIPTION | CLEAN MERCHANT" upper-cased.
BUILTIN = [
    # ── transfers between your own stuff ──
    (r"PAYMENT.{0,10}THANK YOU|PAIEMENT.{0,10}MERCI|CREDIT CARD PAYMENT|CREDIT CARD PMT|\bCC PAYMENT|MASTERCARD PAYMENT|VISA PAYMENT|PAYMENT RECEIVED|^PAYMENT\b|AUTOPAY|MONEY-BACK CREDIT CARD PAYMENT|MB-CREDIT CARD|BILL PAYMENT.*(TANGERINE|MASTERCARD|VISA|NBC|BNC|RBC)", "Credit Card Payment", None),
    (r"WEALTHSIMPLE INVEST|WS INVEST|QUESTRADE|\bTFSA\b|\bRRSP\b|\bFHSA\b|INVESTMENT CONTRIBUTION|BUY .* SHARES|WEALTHSIMPLE TRADE", "Investing", None),
    (r"TRANSFER (TO|FROM)|INTERNET TRANSFER|ONLINE TRANSFER|\bTFR-(TO|FR)|WWW TRF|INTERNAL TRANSFER|TRANSFER (IN|OUT)\b|VIREMENT ENTRE|ACCOUNT TRANSFER|^TRANSFER\b", "Internal Transfer", None),
    # ── income ──
    (r"PAYROLL|\bPAY\b.*\bPAY\b|DIRECT DEPOSIT|D[EÉ]P[OÔ]T DIRECT|SALARY|\bFORD MOTOR|FORD OF CANADA|\bPAYROLL DEP", "Salary", IN),
    (r"\bGST\b|\bHST\b|CANADA FED|CANADA PRO|GOV'?T CANADA|GOVT CANADA|\bCRA\b|TRILLIUM|\bOTB\b|\bCCB\b|NSLSC|\bOSAP\b|EMPLOYMENT INS|\bEI CANADA|CANADA CARBON|CLIMATE ACTION|\bCAIP\b|ONTARIO GOV", "Government & Benefits", IN),
    (r"INTEREST (PAID|EARNED|CREDIT)|^INTEREST\b|\bINT PAID|INTÉRÊTS", "Interest", IN),
    (r"MONEY-BACK REWARDS|CASH ?BACK|REWARDS (DEPOSIT|REDEMPTION|CREDIT)", "Cashback & Rewards", IN),
    # ── fees ──
    (r"INTEREST CHARGE|PURCHASE INTEREST|CASH ADVANCE INT|FINANCE CHARGE", "Interest Charges", OUT),
    (r"SERVICE CHARGE|MONTHLY FEE|ACCOUNT FEE|\bNSF\b|OVERDRAFT|FX FEE|FOREIGN TRANS|ANNUAL FEE|\bFRAIS\b|E-TRANSFER FEE|\bFEE\b", "Bank Fees", OUT),
    (r"CRA.*TAX|REVENUE CANADA|TAX PAYMENT|\bCRA\b", "Taxes", OUT),
    # ── phone / internet / utilities (before restaurants so PUBLIC MOBILE != pub) ──
    (r"ROGERS|\bBELL (CANADA|MOBILITY)|\bBELL\b|\bFIDO\b|KOODO|VIRGIN (PLUS|MOBILE)|FREEDOM MOBILE|TELUS|PUBLIC MOBILE|CHATR|LUCKY MOBILE|TEKSAVVY|VIDEOTRON|STARLINK|DISTRIBUTEL|NEPTUNE|START\.CA", "Internet & Phone", None),
    (r"HYDRO|ENBRIDGE|UTILIT|ALECTRA|RELIANCE HOME|ENERCARE|CITY OF OTTAWA WATER|WATER BILL|ENOVA", "Utilities", None),
    (r"\bRENT\b|PROPERTY MGMT|PROPERTY MANAGEMENT|REALTY|MINTO|HOMESTEAD|CLV GROUP|TIMBERCREEK|CAPREIT|SKYLINE LIVING|DOMUS|LEASE PAYMENT", "Rent", None),
    # ── food ──
    (r"UBER ?EATS|DOORDASH|SKIP ?THE ?DISHES|SKIPTHEDISHES|FANTUAN|RITUAL\.CO|GRUBHUB", "Food Delivery", None),
    (r"TIM HORTON|STARBUCKS|SECOND CUP|BRIDGEHEAD|\bCOFFEE\b|\bCAFE\b|CAFÉ|BALZAC|PRESOTEA|CHATIME|COCO FRESH|GONG CHA|DAVIDSTEA|BUBBLE TEA|THE ALLEY|KUNG FU TEA", "Coffee", None),
    (r"LCBO|BEER STORE|\bSAQ\b|WINE RACK|BREWERY|WINERY", "Alcohol", None),
    (r"COSTCO GAS|PETRO-?CAN|\bESSO\b|\bSHELL\b|ULTRAMAR|PIONEER|CANADIAN TIRE GAS|GAS BAR|HUSKY|\bIRVING\b|\bMOBIL\b|CIRCLE K|SUNOCO|FAS GAS|ON THE RUN|GAS STATION", "Gas", None),
    (r"LOBLAW|NO ?FRILLS|\bMETRO\b|SOBEYS|FOOD BASICS|FRESHCO|FARM ?BOY|FORTINOS|ZEHRS|SUPERSTORE|REAL CDN|COSTCO|T&T|\bADONIS|YOUR INDEPENDENT|VALU-?MART|GIANT TIGER|INSTACART|WHOLE FOODS|NATIONS FRESH|BULK BARN|SAFEWAY|\bIGA\b|\bMAXI\b|PROVIGO|SUPER C|FOOD MARKET|GROCER|MARCHE|MARKET\b|VOILA", "Groceries", None),
    (r"RESTAURANT|\bGRILL\b|PIZZA|SUSHI|SHAWARMA|BURGER|MCDONALD|\bA&W\b|WENDY|\bKFC\b|SUBWAY|CHIPOTLE|FRESHII|POPEYES|DOMINO|\bPHO\b|RAMEN|\bTHAI\b|KITCHEN|BISTRO|\bBAR\b|\bPUB\b|TACO|\bBBQ\b|WINGS|DINER|EATERY|HARVEY|BOSTON PIZZA|MARY BROWN|OSMOW|BOOSTER JUICE|DAIRY QUEEN|BIRYANI|CURRY|TANDOOR|\bMELT\b|NANDO|BARBURRITO|MUCHO BURRITO|GUAC|POKE|DUMPLING|NOODLE|SMOKE'?S|CHICK-?FIL|FIVE GUYS|PANERA|CRUMBL|TACO BELL|PIZZA PIZZA|LITTLE CAESAR|ST-?HUBERT|SWISS CHALET|MONTANA|KELSEY|EARLS|JOEY|CACTUS CLUB|THE KEG|MILESTONES|SCORES|DENNY|IHOP|\bCORA\b|BAKERY|PATISSERIE|DESSERT|GELATO|ICE CREAM|BEAVERTAILS|SHAWARMA|MANDARIN|BUFFET|HOT POT|KBBQ|CHIPOTLE|STEAKHOUSE|TAVERN|BRASSERIE|FOOD COURT|\bDELI\b", "Restaurants", None),
    # ── transport ──
    (r"PRESTO|OC ?TRANSPO|\bTTC\b|GO TRANSIT|VIA RAIL|\bSTM\b|\bGRT\b|\bO-?TRAIN|MIWAY|\bSTO\b|TRANSIT", "Transit", None),
    (r"\bUBER\b(?!.*EATS)|\bLYFT\b|\bTAXI\b|BLUELINE|COOP TAXI|\bBOLT\b", "Rideshare", None),
    (r"PARKING|IMPARK|INDIGO PARK|PRECISE PARK|\bHONK\b|PAYBYPHONE|407 ?ETR|GREEN P|PARKLINK", "Parking & Tolls", None),
    (r"MR\.? ?LUBE|JIFFY|\bTIRE\b|MIDAS|KAL TIRE|CAR WASH|SERVICE ?ONTARIO|AUTO PARTS|NAPA|PARTSOURCE|AUTO REPAIR|COLLISION|\bDEALER|TOYOTA|HONDA|MAZDA|HYUNDAI|NISSAN|VOLKSWAGEN|\bBMW\b|MERCEDES|AUDI\b|\bKIA\b|SUBARU|LEXUS|TESLA", "Car Maintenance", OUT),
    (r"INSURANCE|\bINTACT\b|TD INSURANCE|BELAIRDIRECT|\bAVIVA\b|DESJARDINS (INS|GEN)|SONNET|CO-?OPERATORS|ECONOMICAL|WAWANESA|ALLSTATE", "Car Insurance", OUT),
    # ── subscriptions ──
    (r"ANTHROPIC|CLAUDE\.AI|OPENAI|CHATGPT|GITHUB|VERCEL|NOTION|ADOBE|MICROSOFT|MSFT|GOOGLE ?\*? ?(STORAGE|ONE|CLOUD|WORKSPACE|GSUITE)|DROPBOX|1PASSWORD|ICLOUD|APPLE\.COM/BILL|JETBRAINS|CURSOR|LEETCODE|MEDIUM\.COM|LINKEDIN|NAMECHEAP|CLOUDFLARE|\bAWS\b|AMAZON WEB SERV|DIGITALOCEAN|CHEGG|GRAMMARLY|PERPLEXITY|MIDJOURNEY|REPLIT|HEROKU|NETLIFY|SUPABASE|RAILWAY|GODADDY|SQUARESPACE|\bWIX\b|CANVA|FIGMA|ZOOM\.US|SLACK|PROTON|NORDVPN|EXPRESSVPN|MULLVAD|DUOLINGO", "Software & AI", None),
    (r"SPOTIFY|NETFLIX|DISNEY|CRAVE|PRIME VIDEO|YOUTUBE|APPLE MUSIC|AUDIBLE|TWITCH|PARAMOUNT|\bDAZN\b|HULU|AMAZON PRIME|PRIME MEMBER|APPLE TV|SIRIUS|TIDAL|DEEZER|CRUNCHYROLL|PATREON|SUBSTACK|ONLYFANS", "Streaming & Music", None),
    # ── health / fitness / care ──
    (r"GOODLIFE|FIT4LESS|PLANET FITNESS|ANYTIME FITNESS|EVOLVE|CRUNCH|\bYMCA\b|\bGYM\b|FITNESS|CLIMB|BOULDER|ATHLETIC|ORANGETHEORY|F45|BARRY'?S|CROSSFIT|SPORTS CENTRE|REC CENTRE", "Gym", None),
    (r"SHOPPERS|\bSDM\b|REXALL|PHARMA|JEAN COUTU|LONDON DRUGS|UNIPRIX|FAMILIPRIX|GUARDIAN DRUG|PHARMASAVE|DRUG MART", "Pharmacy", None),
    (r"\bGNC\b|PROSUPPS|SUPPLEMENT|PROTEIN|BODYBUILDING|POPEYE'?S SUPP|MYPROTEIN|HEALTHY PLANET|NUTRITION|VITAMIN|ALLMAX|IRON VEGAN|NA-?KD|LEGION ATHLETICS|TRANSPARENT LABS|GHOST LIFESTYLE", "Supplements", None),
    (r"DENTAL|DENTIST|ORTHODONT|CLINIC|MEDICAL|PHYSIO|OPTOMETR|OPTICAL|HOSPITAL|LIFELABS|DYNACARE|CHIRO|MASSAGE THERAP|\bRMT\b|PSYCH|COUNSEL|HEALTH CENTRE|WALK-?IN|TELUS HEALTH|MAPLE\.CA", "Medical", None),
    (r"BARBER|SALON|\bHAIR\b|SEPHORA|BODY SHOP|\bSPA\b|\bNAIL|GROOM|BATH ?& ?BODY|KIEHL|L'?OCCITANE|\bULTA\b|FRAGRANCE|COLOGNE|PARFUM|PERFUME|AESOP|THE ORDINARY|DECIEM|\bLUSH\b|SKIN ?CARE|DERMA|HISTORY BARBER", "Grooming", None),
    # ── shopping ──
    (r"AMAZON|AMZN|\bEBAY|ALIEXPRESS|\bTEMU\b|SHEIN|ETSY|WISH\.COM|SHOPIFY|PAYPAL \*", "Online Shopping", None),
    (r"BEST BUY|APPLE STORE|APPLE\.COM(?!/BILL)|APPLE ONLINE|MEMORY EXPRESS|CANADA COMPUTERS|NEWEGG|THE SOURCE|MICROSOFT STORE|SAMSUNG|\bDELL\b|LENOVO|\bB&H\b|VISIONS ELECTRONICS|STAPLES|\bLOGITECH|RAZER|STEELSERIES", "Electronics", None),
    (r"UNIQLO|\bH ?& ?M\b|\bZARA\b|SIMONS|WINNERS|MARSHALLS|SPORT ?CHEK|LULULEMON|\bNIKE\b|ADIDAS|\bGAP\b|OLD NAVY|ARITZIA|SSENSE|HUDSON'?S BAY|\bTHE BAY\b|BANANA REPUBLIC|J\.?CREW|CLUB MONACO|\bROOTS\b|MARK'?S\b|GARAGE CLOTHING|DYNAMITE|FOOT LOCKER|SOFTMOC|\bALDO\b|BROWNS SHOES|\bCOS\b|MASSIMO DUTTI|TOMMY HILFIGER|RALPH LAUREN|LEVI|PATAGONIA|ARC'?TERYX|NORDSTROM|HARRY ROSEN|SUITSUPPLY|INDOCHINO|TIP TOP|MOORES|\bASOS\b|ZALANDO|MR PORTER|NEW BALANCE|ON RUNNING|HOKA|COLUMBIA SPORT|\bMEC\b|ATMOSPHERE|ANTHROPOLOGIE|URBAN OUTFITTERS|AMERICAN EAGLE|ABERCROMBIE|HOLLISTER|BOSS\b|HUGO BOSS|GUESS\b|CALVIN KLEIN|FRANK AND OAK|FRANK & OAK|KOTN|EVERLANE", "Clothing", None),
    (r"\bIKEA\b|HOME DEPOT|DOLLARAMA|DOLLAR TREE|BED BATH|HOMESENSE|LOWE'?S|\bRONA\b|STRUCTURE|STRUCTUBE|WAYFAIR|CANADIAN TIRE|WALMART|WAL-MART|LONDON DRUG|HOME HARDWARE|LINEN CHEST|KITCHEN STUFF|TARGET\b", "General Shopping", None),
    # ── fun ──
    (r"CINEPLEX|LANDMARK CINEMA|TICKETMASTER|EVENTBRITE|STUBHUB|BOWLING|ESCAPE ROOM|MUSEUM|\bAMC\b|CINEMA|THEATRE|THEATER|CONCERT|LIVE NATION|SENATORS|REDBLACKS|TOPGOLF|GO-?KART|ARCADE|DAVE ?& ?BUSTER|COMEDY|NIGHTCLUB|LOUNGE", "Entertainment", None),
    (r"\bSTEAM\b|STEAMPOWERED|PLAYSTATION|SONY INTERACTIVE|\bXBOX\b|NINTENDO|EPIC GAMES|RIOT GAMES|BLIZZARD|\bEA \*|ELECTRONIC ARTS|ROBLOX|VALORANT|ACTIVISION|UBISOFT|ROCKSTAR|GAMESTOP|EB GAMES", "Games", None),
    # ── travel ──
    (r"AIR CANADA|WESTJET|PORTER AIR|\bFLAIR\b|SWOOP|DELTA AIR|UNITED AIR|AMERICAN AIR|EXPEDIA|FLIGHTHUB|SUNWING|AIR TRANSAT|JETBLUE|SOUTHWEST|SPIRIT AIR|ALASKA AIR|LUFTHANSA|BRITISH AIRWAYS|EMIRATES|QATAR|AIR INDIA|KAYAK|SKYSCANNER|AIRLINE", "Flights", None),
    (r"HOTEL|MARRIOTT|HILTON|AIRBNB|BOOKING\.COM|HYATT|\bIHG\b|BEST WESTERN|HOLIDAY INN|LOPESAN|FAIRMONT|SHERATON|WESTIN|RADISSON|HOTELS\.COM|VRBO|MOTEL|\bINN\b|RESORT|HOSTEL", "Hotels", None),
    (r"NEXUS|ARRIVECAN|DUTY FREE|TRAVEL INS|PASSPORT|\bVISA FEE|ENTERPRISE RENT|HERTZ|\bAVIS\b|BUDGET RENT|NATIONAL CAR|TURO|GREYHOUND|FLIXBUS|MEGABUS|AMTRAK", "Travel Other", None),
    # ── education ──
    (r"CARLETON|UNIVERSITY|COLLEGE|TUITION|COURSERA|UDEMY|\bEDX\b|PEARSON|MCGRAW|CENGAGE|WILEY|TOP ?HAT|CROWDMARK|GRADESCOPE|KHAN|BRILLIANT\.ORG|CODECADEMY|EDUCATIVE|EXAM|TEXTBOOK", "Tuition & School", None),
    (r"INDIGO BOOKS|INDIGO\.CA|CHAPTERS|KINDLE|BOOKSTORE|BOOK DEPOT|ABEBOOKS|KOBO", "Books", None),
    # ── giving ──
    (r"DONATION|CHARITY|CANADAHELPS|GOFUNDME|RED CROSS|UNICEF|UNITED WAY|TEMPLE|MANDIR|CHURCH|MOSQUE|GURDWARA|\bSEVA\b", "Donations", None),
    (r"\bGIFT\b|FLOWERS|FLORIST|HALLMARK|1-?800-?FLOWERS|PARTY CITY", "Gifts", None),
]
_COMPILED = [(re.compile(p, re.I), c, d) for p, c, d in BUILTIN]


class Categorizer:
    def __init__(self, con, cfg):
        self.con = con
        self.cfg = cfg
        self.me = my_names(cfg)
        self.cat_kind = {r["name"]: r["kind"] for r in con.execute("SELECT name, kind FROM categories")}
        self.reload_rules()
        self.income_from = cfg["me"].get("income_when_moved_from", {}) or {}
        self.people = {}
        for p in cfg.get("people", []):
            if p.get("name"):
                self.people[p["name"].strip().upper()] = p

    def reload_rules(self):
        self.rules = [dict(r) for r in self.con.execute("SELECT * FROM rules ORDER BY origin='ai', id DESC")]

    # ---- helpers ----
    def is_me(self, name):
        if not name:
            return False
        n = name.strip().upper()
        if n in self.me:
            return True
        # tolerate middle-name variations: all tokens of one of your names present
        toks = set(n.split())
        return any(set(m.split()) <= toks or toks <= set(m.split()) and len(toks) >= 2 for m in self.me)

    def _kind_for(self, category, amount, explicit=None, person=False):
        if explicit:
            return explicit
        k = self.cat_kind.get(category, "expense")
        if k == "expense" and amount > 0:
            return "reimbursement" if person else "refund"
        if k == "income" and amount < 0:
            return "expense"
        return k

    def _rule_matches(self, r, tx):
        if r["direction"] == IN and tx["amount"] < 0 or r["direction"] == OUT and tx["amount"] > 0:
            return False
        val = (tx.get(r["field"] or "merchant") or "")
        if r["is_regex"]:
            try:
                return re.search(r["pattern"], val, re.I) is not None
            except re.error:
                return False
        return val.strip().upper() == r["pattern"].strip().upper()

    # ---- main ----
    def categorize(self, tx, account_type=None):
        """Mutates tx: sets category, kind, cat_source (+ maybe merchant rename). Returns tx."""
        amt = tx["amount"]
        cp = tx.get("counterparty")
        hay = f"{tx.get('description') or ''} | {tx.get('merchant') or ''} | {tx.get('memo') or ''}".upper()
        hay = hay + " | " + re.sub(r"\s*\*\s*", " ", hay)

        # 2. self / people
        if cp and self.is_me(cp):
            src = (tx.get("from_institution") or "").lower()
            for inst, cat in self.income_from.items():
                if amt > 0 and src and inst.lower() in src:
                    return self._set(tx, cat, "income", "rule")  # e.g. govt money parked at RBC, moved to Tangerine
            return self._set(tx, "Internal Transfer", "transfer", "rule")
        # an e-transfer message ("hydro", "groceries") is the best evidence of what it was for
        if cp and tx.get("memo"):
            for rx, cat, d in _COMPILED:
                if rx.search(tx["memo"]) and self.cat_kind.get(cat) == "expense":
                    return self._set(tx, cat, self._kind_for(cat, amt, None, True), "default")
        if cp and cp.upper() in self.people:
            p = self.people[cp.upper()]
            spec = p.get("incoming" if amt > 0 else "outgoing")
            for sp in (spec if isinstance(spec, list) else [spec] if spec else []):
                a = abs(amt)
                if "amount" in sp and abs(a - float(sp["amount"])) > float(sp.get("tolerance", 1)):
                    continue
                if "amount_min" in sp and a < float(sp["amount_min"]):
                    continue
                if "amount_max" in sp and a > float(sp["amount_max"]):
                    continue
                if sp.get("category"):
                    return self._set(tx, sp["category"], self._kind_for(sp["category"], amt, sp.get("kind"), True), "rule")

        # 3. explicit rules
        for r in self.rules:
            if self._rule_matches(r, tx):
                self.con.execute("UPDATE rules SET hits = hits + 1 WHERE id=?", (r["id"],))
                if r["rename"]:
                    tx["merchant"] = r["rename"]
                return self._set(tx, r["category"], self._kind_for(r["category"], amt, r["kind"], bool(cp)), "ai" if r["origin"] == "ai" else "rule")

        # 4. learned from your past manual edits on the same merchant / person
        key, val = ("counterparty", cp) if cp else ("merchant", tx.get("merchant"))
        if val:
            row = self.con.execute(
                f"SELECT category, kind FROM transactions WHERE {key}=? AND cat_source='manual' AND (amount>0)=? ORDER BY updated_at DESC LIMIT 1",
                (val, amt > 0),
            ).fetchone()
            if row:
                return self._set(tx, row["category"], row["kind"], "learned")

        # e-transfer memo hints ("rent", "groceries", "uber"...)
        if cp and tx.get("memo"):
            for rx, cat, d in _COMPILED:
                if rx.search(tx["memo"]) and self.cat_kind.get(cat) == "expense":
                    return self._set(tx, cat, self._kind_for(cat, amt, None, True), "default")

        # 5. built-ins (skip for e-transfers to/from people: a name like "Rogers" shouldn't become a phone bill)
        if not cp:
            for rx, cat, d in _COMPILED:
                if d == IN and amt < 0 or d == OUT and amt > 0:
                    continue
                if rx.search(hay):
                    return self._set(tx, cat, self._kind_for(cat, amt), "default")

        # 6. fallbacks
        if cp:
            if amt > 0:
                return self._set(tx, "From People", "income", "default", review=True)
            return self._set(tx, "Paid to People", "expense", "default", review=True)
        if amt > 0:
            # a credit on a merchant we've seen spending at → refund
            prev = self.con.execute(
                "SELECT category FROM transactions WHERE merchant=? AND amount<0 AND category NOT IN ('Uncategorized') ORDER BY date DESC LIMIT 1",
                (tx.get("merchant"),),
            ).fetchone()
            if prev:
                return self._set(tx, prev["category"], "refund", "default")
            if account_type == "credit":
                return self._set(tx, "Misc", "refund", "none", review=True)
            return self._set(tx, "Other Income", "income", "none", review=True)
        return self._set(tx, "Uncategorized", "expense", "none", review=True)

    @staticmethod
    def _set(tx, category, kind, source, review=False):
        tx["category"], tx["kind"], tx["cat_source"] = category, kind, source
        tx["_needs_review"] = review
        return tx


def recategorize_all(con, cfg, include_ai=False):
    """Re-run rules over everything that wasn't set by hand. Returns count changed."""
    cz = Categorizer(con, cfg)
    acct_type = {r["id"]: r["type"] for r in con.execute("SELECT id, type FROM accounts")}
    changed = 0
    for row in con.execute("SELECT * FROM transactions WHERE cat_source != 'manual' OR cat_source IS NULL").fetchall():
        if row["link_id"] and row["category"] in ("Internal Transfer", "Credit Card Payment", "Investing"):
            continue  # paired transfer; leave it
        tx = dict(row)
        before = (tx["category"], tx["kind"], tx["merchant"])
        if tx["cat_source"] == "ai" and not include_ai:
            # keep AI answers unless a stronger rule now applies
            pass
        cz.categorize(tx, acct_type.get(tx["account_id"]))
        if (tx["category"], tx["kind"], tx["merchant"]) != before:
            con.execute(
                "UPDATE transactions SET category=?, kind=?, cat_source=?, merchant=?, updated_at=datetime('now') WHERE id=?",
                (tx["category"], tx["kind"], tx["cat_source"], tx["merchant"], tx["id"]),
            )
            changed += 1
    con.commit()
    return changed
