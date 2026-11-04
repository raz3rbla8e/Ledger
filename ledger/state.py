"""Small bits of settings that deep helpers need without threading cfg through every call."""
CANCELLED = set()
FIXED_TOLD = {}   # [plan.fixed] — e.g. what you plan to send Dad each month  # subscriptions/bills you've told Ledger you stopped ([plan] cancelled = ["Planet Fitness"])


def is_cancelled(merchant):
    m = (merchant or "").lower()
    return any(c and (c == m or c in m) for c in CANCELLED)
