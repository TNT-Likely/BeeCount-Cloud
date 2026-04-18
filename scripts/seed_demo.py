"""Bootstrap seed users for a fresh self-hosted deployment.

Creates two accounts(密码都是 `123456`):
- `owner@example.com` —— admin,用于登录 `/admin/*` 控制台和日常使用
- `test@example.com` —— 普通用户,用于验证多用户数据隔离(开发测试)

Previously this script also seeded a demo ledger, device, transactions and
category fixtures — removed per the single-user-per-ledger / no-sample-data
policy. Run this once after ``alembic upgrade head`` to get a login, then let
the real app populate ledgers and data.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database import SessionLocal
from src.models import User
from src.security import hash_password


def _upsert_user(db: Session, *, email: str, password: str, is_admin: bool) -> None:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email,
            password_hash=hash_password(password),
            is_admin=is_admin,
        )
        db.add(user)
    else:
        # 已存在则对齐 is_admin。密码不覆盖 —— 留给用户自己改。
        if user.is_admin != is_admin:
            user.is_admin = is_admin


def main() -> None:
    db = SessionLocal()
    try:
        _upsert_user(db, email="owner@example.com", password="123456", is_admin=True)
        _upsert_user(db, email="test@example.com", password="123456", is_admin=False)

        db.commit()
        print("seed completed")
        print("  owner (admin):  owner@example.com / 123456")
        print("  test  (普通):   test@example.com  / 123456")
    finally:
        db.close()


if __name__ == "__main__":
    main()
