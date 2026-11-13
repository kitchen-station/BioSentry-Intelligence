"""Passphrase hashing (scrypt from the standard library) and the command that sets a passphrase.

    python -m biosentry.db.passphrase <clinician_id>        (make db-passphrase id=<clinician_id>)

The passphrase is read with getpass, or from stdin when that is not a terminal; at least 12
characters. BIOSENTRY_DATABASE_URL names the database. A stored value carries its own parameters
(`scrypt$n$r$p$salt$hash`), so the cost can be raised later without locking anyone out. A
clinician with no stored hash cannot sign in.
"""

import argparse
import base64
import getpass
import hashlib
import hmac
import secrets
import sys
import threading
from functools import cache

from biosentry.core.config import Settings
from biosentry.repositories.postgres import connect, transaction

N, R, P = 2**15, 8, 1
MIN_LENGTH, MAX_LENGTH = 12, 256  # the API refuses a longer one before hashing it
_MAXMEM = 2**26  # n=2**15, r=8 needs exactly hashlib's default 32 MiB limit, so ask for 64
# Each scrypt takes 32 MiB and a core for ~100 ms, and sign-ins for distinct ids do not wait on
# each other, so a flood of them would eat the machine. At most this many run at once, process-wide;
# the rest wait, at most SLOT_WAIT seconds: then `Busy` (the API answers 503), so a flood of sign-ins
# cannot hold every request thread and connection while it queues.
SLOT_WAIT = 2.0
_SLOTS = threading.BoundedSemaphore(2)


class Busy(RuntimeError):
    """No scrypt slot came free in time. Nothing was hashed."""


def _scrypt(passphrase: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
    if not _SLOTS.acquire(timeout=SLOT_WAIT):
        raise Busy
    try:  # held only for the hash
        return hashlib.scrypt(
            passphrase.encode(), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=length
        )
    finally:
        _SLOTS.release()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_passphrase(passphrase: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(passphrase, salt, N, R, P, 32)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


@cache
def _dummy() -> str:
    return hash_passphrase("a passphrase nobody has")


def verify(passphrase: str, stored: str | None) -> bool:
    """Whether the passphrase matches. With no stored hash (or one this code did not write) it
    still hashes, against a dummy, so every sign-in costs the same time, and answers False."""
    parts = (stored or "").split("$")
    real = len(parts) == 6 and parts[0] == "scrypt"
    _, n, r, p, salt, digest = parts if real else _dummy().split("$")
    expected = base64.b64decode(digest)
    got = _scrypt(passphrase, base64.b64decode(salt), int(n), int(r), int(p), len(expected))
    return hmac.compare_digest(got, expected) and real


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clinician_id")
    args = parser.parse_args(argv)
    if sys.stdin.isatty():
        passphrase = getpass.getpass("Passphrase: ")
        if getpass.getpass("Again: ") != passphrase:
            raise SystemExit("the two entries differ")
    else:
        passphrase = sys.stdin.readline().rstrip("\r\n")
    if not MIN_LENGTH <= len(passphrase) <= MAX_LENGTH or "\x00" in passphrase:
        raise SystemExit(f"the passphrase must be {MIN_LENGTH} to {MAX_LENGTH} characters")
    stored = hash_passphrase(passphrase)
    with connect(Settings.from_env().database_url) as conn, transaction(conn):
        found = conn.execute(
            "UPDATE clinician SET passphrase_hash = %s WHERE id = %s", (stored, args.clinician_id)
        ).rowcount
        # A token issued under the old passphrase must not outlive it.
        conn.execute("DELETE FROM session WHERE clinician_id = %s", (args.clinician_id,))
    if not found:
        raise SystemExit("no such clinician")
    print("passphrase set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
