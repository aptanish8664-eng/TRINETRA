from __future__ import annotations

import argparse
import getpass
import sys
import uuid

from .security import bootstrap_general


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision the initial TRINETRA General account interactively."
    )
    parser.add_argument("--username", required=True, help="Unique sign-in username")
    parser.add_argument("--full-name", required=True, help="Full name displayed in the application")
    args = parser.parse_args()

    password = getpass.getpass("New password (12-128 characters): ")
    confirmation = getpass.getpass("Confirm new password: ")
    if password != confirmation:
        print("Passwords did not match.", file=sys.stderr)
        return 2
    try:
        user = bootstrap_general(
            username=args.username,
            full_name=args.full_name,
            password=password,
            request_id=f"cli-{uuid.uuid4().hex}",
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Provisioned initial {user.role} user {user.username} ({user.id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
