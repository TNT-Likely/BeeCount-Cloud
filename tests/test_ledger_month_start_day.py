"""ledgers.month_start_day(自定义每月起始日)同步契约 — push 侧:

- mobile push 的 ledger upsert payload 带 `monthStartDay` → 落 Ledger 列(clamp 1-28)
- payload 不带该 key 时保持原值(partial-update merge 契约,防漏 merge 类 bug)
- 非 int(含 bool)忽略

read 端 / 快照 / web 写端的契约测试由后续任务追加到本文件。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Ledger


def _make_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app), TS


def _register(client: TestClient, email: str, client_type: str = "app") -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": client_type,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _seed_ledger(client: TestClient, token: str, device_id: str, ledger_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    content = (
        f'{{"ledgerName":"{ledger_id}","currency":"CNY","count":0,'
        '"items":[],"accounts":[],"categories":[],"tags":[]}'
    )
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": "ledger_snapshot",
                    "entity_sync_id": ledger_id,
                    "action": "upsert",
                    "payload": {"content": content},
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _push_ledger_upsert(
    client: TestClient, token: str, device_id: str, ledger_id: str, payload: dict
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": "ledger",
                    "entity_sync_id": ledger_id,
                    "action": "upsert",
                    "payload": payload,
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _ledger_row(TS, external_id: str) -> Ledger:
    with TS() as db:
        row = db.scalar(select(Ledger).where(Ledger.external_id == external_id))
        assert row is not None
        db.expunge(row)
        return row


def test_mobile_push_ledger_month_start_day_applies() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "msd1@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_MSD1")
        _push_ledger_upsert(
            client, token, device, "L_MSD1",
            {"syncId": "L_MSD1", "ledgerName": "L_MSD1", "currency": "CNY",
             "monthStartDay": 15},
        )
        assert _ledger_row(TS, "L_MSD1").month_start_day == 15
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_ledger_partial_update_keeps_month_start_day() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "msd2@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_MSD2")
        _push_ledger_upsert(
            client, token, device, "L_MSD2",
            {"syncId": "L_MSD2", "ledgerName": "L_MSD2", "currency": "CNY",
             "monthStartDay": 15},
        )
        # 老版本 App 改名:payload 不带 monthStartDay → 不得被重置
        _push_ledger_upsert(
            client, token, device, "L_MSD2",
            {"syncId": "L_MSD2", "ledgerName": "改名后", "currency": "CNY"},
        )
        row = _ledger_row(TS, "L_MSD2")
        assert row.name == "改名后"
        assert row.month_start_day == 15
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_ledger_month_start_day_clamped() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "msd3@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_MSD3")
        _push_ledger_upsert(
            client, token, device, "L_MSD3",
            {"syncId": "L_MSD3", "ledgerName": "L_MSD3", "currency": "CNY",
             "monthStartDay": 99},
        )
        assert _ledger_row(TS, "L_MSD3").month_start_day == 28
        _push_ledger_upsert(
            client, token, device, "L_MSD3",
            {"syncId": "L_MSD3", "ledgerName": "L_MSD3", "currency": "CNY",
             "monthStartDay": 0},
        )
        assert _ledger_row(TS, "L_MSD3").month_start_day == 1
        # 非 int(含 bool/字符串)忽略,保持原值
        _push_ledger_upsert(
            client, token, device, "L_MSD3",
            {"syncId": "L_MSD3", "ledgerName": "L_MSD3", "currency": "CNY",
             "monthStartDay": True},
        )
        assert _ledger_row(TS, "L_MSD3").month_start_day == 1
    finally:
        app.dependency_overrides.clear()
