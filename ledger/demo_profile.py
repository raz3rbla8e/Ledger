"""A fictional profile with the same shape as a real one — tests and the demo never use real personal data."""
PROFILE = {
    "me": {"name": "Jordan", "names": ["Jordan Avery Example", "Jordan Example", "Jordan Avery"],
           "income_when_moved_from": {"RBC": "Government & Benefits"}},
    "people": [
        {"name": "PAT LANDLORD", "outgoing": {"category": "Rent"}},
        {"name": "DAD", "outgoing": {"category": "Family Support"}},
        {"name": "SAM ROOMIE", "incoming": [{"amount_min": 790, "amount_max": 810, "category": "Rent", "kind": "reimbursement"},
                                           {"category": "Groceries", "kind": "reimbursement"}],
         "outgoing": {"category": "Groceries"}},
    ],
    "events": [
        {"name": "Co-op ends — last paycheque", "kind": "stop", "date": "2026-12-31", "category": "Salary"},
        {"name": "No money to Dad in December", "kind": "pause", "start": "2026-12-01", "end": "2026-12-31", "category": "Family Support"},
        {"name": "Pay Dad back", "kind": "one_time", "date": "2026-12-15", "amount": -600, "category": "Debt Repayment"},
        {"name": "Winter trip", "kind": "trip", "start": "2026-12-13", "end": "2026-12-21", "budget": 0},
        {"name": "April rent already paid", "kind": "pause", "start": "2027-04-01", "end": "2027-04-30", "category": "Rent"},
        {"name": "Move home — rent & parking end", "kind": "stop", "date": "2027-05-01", "category": ["Rent", "Parking & Tolls", "Utilities", "Shared Costs"]},
    ],
    "goals": [
        {"name": "Emergency fund", "target": "3x_fixed", "priority": 1},
        {"name": "Pay Dad back (PS5)", "target": 600, "due": "2026-12-15", "priority": 2},
        {"name": "Make it to the first full-time paycheque", "target": "runway", "due": "2027-09-01", "priority": 3},
    ],
    "horizon": "2027-09-30",
    "applepay_cards": {"Tangerine": "tangerine-cc-8888"},
}


def apply(cfg):
    cfg["me"].update(PROFILE["me"])
    cfg["people"] = PROFILE["people"]
    cfg["plan"]["events"] = PROFILE["events"]
    cfg["plan"]["horizon"] = PROFILE["horizon"]
    cfg["goals"] = PROFILE["goals"]
    cfg["applepay"]["cards"] = PROFILE["applepay_cards"]
    cfg["email"]["inboxes"] = [{"address": "you@example.com", "password_env": "LEDGER_IMAP_PASSWORD"},
                               {"address": "you2@example.com", "password_env": "LEDGER_IMAP_PASSWORD_2"}]
    return cfg
