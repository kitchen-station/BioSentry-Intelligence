"""The command that grants and revokes an admin. No route does this: it needs database access.

    python -m biosentry.db.admin grant <admin-id>
    python -m biosentry.db.admin revoke <admin-id>

`grant` adds a new clinician whose id starts `admin-`; it never promotes an existing clinician,
who would keep their patients. The admin cannot sign in until `db.passphrase` sets a passphrase.
`revoke` deletes the admin and their sessions and keeps their audit rows.
BIOSENTRY_DATABASE_URL names the database.
"""

import argparse
import re

from biosentry.core.config import Settings
from biosentry.repositories.postgres import ADMIN_ROLE, connect, transaction

ADMIN_ID = re.compile(r"admin-[a-z0-9-]{1,58}")  # 64 characters at most, as a sign-in id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("grant", "revoke"))
    parser.add_argument("admin_id")
    args = parser.parse_args(argv)
    if not ADMIN_ID.fullmatch(args.admin_id):
        raise SystemExit("an admin id is admin- then lowercase letters, digits and hyphens")
    with connect(Settings.from_env().database_url) as conn, transaction(conn):
        if args.command == "grant":
            made = conn.execute(
                "INSERT INTO clinician (id, role, passphrase_hash) VALUES (%s, %s, NULL)"
                " ON CONFLICT DO NOTHING",
                (args.admin_id, ADMIN_ROLE),
            ).rowcount
            if not made:
                raise SystemExit("that id already exists")
            print(
                f"admin {args.admin_id} granted. It cannot sign in until a passphrase is set:"
                f" python -m biosentry.db.passphrase {args.admin_id}"
            )
        else:
            conn.execute(
                "DELETE FROM session WHERE clinician_id IN"
                " (SELECT id FROM clinician WHERE id = %s AND role = %s)",
                (args.admin_id, ADMIN_ROLE),
            )
            gone = conn.execute(
                "DELETE FROM clinician WHERE id = %s AND role = %s", (args.admin_id, ADMIN_ROLE)
            ).rowcount
            if not gone:
                raise SystemExit("no such admin")
            print(f"admin {args.admin_id} revoked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
