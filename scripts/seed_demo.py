"""Bootstrap a single admin user for a fresh self-hosted deployment.

Previously this script also seeded a demo ledger, device, transactions and
category fixtures — removed per the single-user-per-ledger / no-sample-data
policy. Run this once after ``alembic upgrade head`` to get a login, then let
the real app populate ledgers and data.
"""

from __future__ import annotations

from sqlalchemy import select

from src.database import SessionLocal
from src.models import User
from src.security import hash_password


def main() -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        if user is None:
            user = User(
                email="owner@example.com",
                password_hash=hash_password("123456"),
                is_admin=True,
            )
            db.add(user)
        elif not user.is_admin:
            user.is_admin = True

        db.commit()
        print("seed completed")
        print("owner email: owner@example.com")
        print("owner password: 123456")
    finally:
        db.close()


if __name__ == "__main__":
    main()
