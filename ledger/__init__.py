import sys

__version__ = "3.0.0"

if sys.version_info < (3, 11):
    raise SystemExit("Ledger needs Python 3.11 or newer — install it with:  winget install Python.Python.3.12")
