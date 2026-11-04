"""Rotating log file at ~/Ledger/logs/ledger.log — so a hidden background process never fails silently."""
import logging
import logging.handlers
import re

_SECRETISH = re.compile(r"(password|token|secret|key)=\S+", re.I)


class _Redact(logging.Filter):
    def filter(self, record):
        record.msg = _SECRETISH.sub(r"\1=***", str(record.msg))
        return True


def setup(home, console=True):
    d = home / "logs"
    d.mkdir(exist_ok=True)
    lg = logging.getLogger("ledger")
    if lg.handlers:
        return lg
    lg.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(d / "ledger.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    fh.addFilter(_Redact())
    lg.addHandler(fh)
    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(message)s"))
        sh.addFilter(_Redact())
        lg.addHandler(sh)
    return lg
