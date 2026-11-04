"""Secrets live in the OS keychain, not in a text file.

Windows → Credential Manager (DPAPI-encrypted, tied to your Windows login)
macOS   → Keychain (via the `security` tool)
other   → falls back to ~/Ledger/.env (chmod 600)

Lookup order for every secret: real environment variable → OS keychain → .env file.
"""
import os
import subprocess
import sys

SECRET_NAMES = [
    "LEDGER_IMAP_PASSWORD", "LEDGER_IMAP_PASSWORD_2", "LEDGER_IMAP_PASSWORD_3",
    "LEDGER_DASHBOARD_PASSWORD", "LEDGER_HOOK_TOKEN", "LEDGER_WIDGET_TOKEN",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY",
]
SERVICE = "Ledger"


# ───────────────────────── Windows Credential Manager ─────────────────────────
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes as wt

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR), ("Comment", wt.LPWSTR),
                    ("LastWritten", wt.FILETIME), ("CredentialBlobSize", wt.DWORD), ("CredentialBlob", ctypes.c_void_p),
                    ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wt.LPWSTR), ("UserName", wt.LPWSTR)]

    _adv = ctypes.WinDLL("advapi32", use_last_error=True)
    _adv.CredReadW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    _adv.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wt.DWORD]
    _adv.CredDeleteW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD]
    _adv.CredFree.argtypes = [ctypes.c_void_p]
    _GENERIC, _PERSIST_LOCAL = 1, 2

    def _get(name):
        p = ctypes.POINTER(_CREDENTIAL)()
        if not _adv.CredReadW(f"{SERVICE}/{name}", _GENERIC, 0, ctypes.byref(p)):
            return None
        try:
            c = p.contents
            return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
        finally:
            _adv.CredFree(p)

    def _set(name, value):
        blob = value.encode("utf-16-le")
        buf = ctypes.create_string_buffer(blob, len(blob))
        c = _CREDENTIAL()
        c.Type = _GENERIC
        c.TargetName = f"{SERVICE}/{name}"
        c.CredentialBlobSize = len(blob)
        c.CredentialBlob = ctypes.cast(buf, ctypes.c_void_p)
        c.Persist = _PERSIST_LOCAL
        c.UserName = "ledger"
        if not _adv.CredWriteW(ctypes.byref(c), 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def _delete(name):
        _adv.CredDeleteW(f"{SERVICE}/{name}", _GENERIC, 0)

    BACKEND = "Windows Credential Manager"

# ───────────────────────── macOS Keychain ─────────────────────────
elif sys.platform == "darwin":
    def _get(name):
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"], capture_output=True, text=True)
        return r.stdout.rstrip("\n") if r.returncode == 0 else None

    def _set(name, value):
        subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE, "-a", name, "-w", value], check=True, capture_output=True)

    def _delete(name):
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", name], capture_output=True)

    BACKEND = "macOS Keychain"

else:
    _get = _set = _delete = None
    BACKEND = ".env file"


def keychain_available():
    return _get is not None and os.environ.get("LEDGER_NO_KEYCHAIN") != "1"


def get(name):
    if os.environ.get(name):
        return os.environ[name]
    if keychain_available():
        try:
            return _get(name)
        except Exception:
            return None
    return None


def set(name, value, env_path=None):  # noqa: A001 — mirrors keyring's API
    """Store a secret. Keychain when available (and strip it from .env); otherwise .env."""
    os.environ[name] = value
    if keychain_available():
        _set(name, value)
        if env_path:
            _env_write(env_path, name, "")
        return BACKEND
    if env_path:
        _env_write(env_path, name, value)
    return ".env file"


def delete(name, env_path=None):
    os.environ.pop(name, None)
    if keychain_available():
        try:
            _delete(name)
        except Exception:
            pass
    if env_path:
        _env_write(env_path, name, "")


def load_into_env():
    """Make keychain secrets visible to the rest of Ledger (this process only)."""
    if not keychain_available():
        return
    for n in SECRET_NAMES:
        if not os.environ.get(n):
            try:
                v = _get(n)
            except Exception:
                v = None
            if v:
                os.environ[n] = v


def migrate_env_file(env_path):
    """Move any secrets found in .env into the keychain and blank them in the file. Returns names moved."""
    if not keychain_available() or not env_path.exists():
        return []
    moved = []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.split(" #")[0].strip().strip('"').strip("'")
        if k in SECRET_NAMES and v:
            _set(k, v)
            _env_write(env_path, k, "")
            moved.append(k)
    return moved


def _env_write(path, key, value):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, done = [], False
    for ln in lines:
        if ln.split("=", 1)[0].strip() == key:
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(ln)
    if not done and value:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
