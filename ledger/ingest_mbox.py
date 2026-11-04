"""Import bank emails from a Gmail export (Google Takeout): a .mbox file, or the Takeout .zip that contains one.

Use it to backfill an inbox Ledger can't log into (no app password): label the bank emails in Gmail, export just
that label at takeout.google.com, and drop the zip in Downloads — or run  python -m ledger import <file>.

Every email goes through the same path as live mail: the authenticity check (Takeout keeps Google's original
Authentication-Results header), the parsers, Message-ID de-duplication. Nothing is extracted to disk.
"""
import mailbox
import os
import tempfile
import zipfile
from pathlib import Path


def _mbox_messages(path):
    """Yield raw bytes for each message in an mbox file."""
    box = mailbox.mbox(str(path), create=False)
    try:
        for key in box.iterkeys():
            yield box.get_bytes(key)
    finally:
        box.close()


def messages(path):
    """Raw messages from a .mbox, or from every .mbox inside a Takeout .zip."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not name.lower().endswith(".mbox"):
                    continue
                # mailbox needs a real file; stream the member to a private temp file and remove it right after
                fd, tmp = tempfile.mkstemp(suffix=".mbox")
                try:
                    with os.fdopen(fd, "wb") as out, z.open(name) as src:
                        while chunk := src.read(1 << 20):
                            out.write(chunk)
                    yield from _mbox_messages(tmp)
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
    else:
        yield from _mbox_messages(path)


def is_mail_export(path):
    path = Path(path)
    if path.suffix.lower() == ".mbox":
        return True
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as z:
                return any(n.lower().endswith(".mbox") for n in z.namelist())
        except (zipfile.BadZipFile, OSError):
            return False
    return False


def import_mail_export(con, cfg, cz, path, log=print):
    """Feed every message through the normal email pipeline. Returns stats."""
    from .ingest_email import SENDERS, ingest_message
    from email.utils import parseaddr
    import email
    stats = {"messages": 0, "skipped": 0}
    for raw in messages(path):
        stats["messages"] += 1
        head = email.message_from_bytes(raw.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0])
        dom = parseaddr(str(head.get("From", "")))[1].lower().rsplit("@", 1)[-1]
        if not any(dom == s or dom.endswith("." + s) for s in SENDERS):
            stats["skipped"] += 1  # not from a bank: never parsed, never stored
            continue
        ingest_message(con, cfg, cz, raw, stats)
        if stats["messages"] % 100 == 0:
            con.commit()
    con.commit()
    log(f"  mail export {Path(path).name}: {stats.get('new', 0)} new transactions from {stats['messages']} emails")
    return stats
