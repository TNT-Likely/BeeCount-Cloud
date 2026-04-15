from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from src.database import SessionLocal
from src.models import Device, Ledger, SyncChange, User
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
            db.flush()
        elif not user.is_admin:
            user.is_admin = True

        device = db.scalar(select(Device).where(Device.user_id == user.id))
        if device is None:
            db.add(
                Device(
                    user_id=user.id,
                    name="Demo Device",
                    platform="ios",
                    last_seen_at=datetime.now(timezone.utc),
                )
            )

        ledger = db.scalar(select(Ledger).where(Ledger.external_id == "demo-ledger"))
        if ledger is None:
            ledger = Ledger(user_id=user.id, external_id="demo-ledger", name="家庭账本")
            db.add(ledger)
            db.flush()

        payload = {
            "content": json.dumps(
                {
                    "ledgerName": "家庭账本",
                    "currency": "CNY",
                    "count": 2,
                    "items": [
                        {
                            "type": "expense",
                            "amount": 32.5,
                            "happenedAt": datetime.now(timezone.utc).isoformat(),
                            "note": "午餐",
                            "categoryName": "餐饮",
                            "categoryKind": "expense",
                        },
                        {
                            "type": "income",
                            "amount": 5000,
                            "happenedAt": datetime.now(timezone.utc).isoformat(),
                            "note": "工资",
                            "categoryName": "工资",
                            "categoryKind": "income",
                        },
                    ],
                    "accounts": [{"name": "现金", "type": "cash", "currency": "CNY"}],
                    "categories": [{"name": "餐饮", "kind": "expense", "level": 1, "sortOrder": 1}],
                    "tags": [{"name": "工作", "color": "#4f46e5"}],
                },
                ensure_ascii=False,
            )
        }
        change = SyncChange(
            user_id=ledger.user_id,
            ledger_id=ledger.id,
            entity_type="ledger_snapshot",
            entity_sync_id=ledger.external_id,
            action="upsert",
            payload_json=payload,
            updated_at=datetime.now(timezone.utc),
            updated_by_user_id=user.id,
        )
        db.add(change)
        db.commit()
        print("seed completed")
        print("owner email: owner@example.com")
        print("owner password: 123456")
        print(f"ledger id: {ledger.external_id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
