"""Runs only on Windows CI: store/read/delete a throwaway secret in Credential Manager."""
import sys

if sys.platform != "win32":
    print("skipped (not Windows)")
    raise SystemExit(0)
sys.path.insert(0, ".")
from ledger import secrets_store as s  # noqa: E402

name = "LEDGER_TEST_SECRET"
s.SECRET_NAMES.append(name)
s._set(name, "t3st-välue-🔒")
assert s._get(name) == "t3st-välue-🔒"
s._delete(name)
assert s._get(name) is None
print("Windows Credential Manager OK")
