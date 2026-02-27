from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from src.database import SessionLocal
from src.models import User


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grant platform admin role to an existing user.")
    parser.add_argument("--email", required=True, help="User email to promote")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_email = args.email.strip().lower()
    if not target_email:
        print("email is required", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == target_email))
        if user is None:
            print(f"user not found: {target_email}", file=sys.stderr)
            return 1

        if user.is_admin:
            print(f"user is already admin: {target_email}")
            return 0

        user.is_admin = True
        db.add(user)
        db.commit()
        print(f"granted admin: {target_email}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
