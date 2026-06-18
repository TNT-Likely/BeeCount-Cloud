"""Task 5: 交易读/写 schema + Web 写端点支持 exclude 标记。

覆盖:
  - web POST /write/ledgers/{id}/transactions 接收 exclude_from_stats /
    exclude_from_budget,RESPONSE(经 read 端点)能读回这两个布尔。
  - 发出的 SyncChange payload 用 camelCase excludeFromStats / excludeFromBudget
    (跟 mobile serializer + Task 2 _LEDGER_MERGE_SPECS 对齐)。
  - projection 行落库 exclude_from_stats / exclude_from_budget。
  - web PATCH 切换某个 flag,另一个不传时保持不变。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Ledger, ReadTxProjection, SyncChange


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


def _iso(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat()


def _register(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "app",
            "device_name": "pytest-app",
            "platform": "app",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _login_web(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _seed_ledger(client: TestClient, token: str, device_id: str, ledger_id: str) -> None:
    now = _iso()
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


def _ledger_internal_id(TS, external_id: str) -> str:
    with TS() as db:
        return db.scalar(select(Ledger.id).where(Ledger.external_id == external_id))


def _base_change_id(client: TestClient, web_token: str, ledger_id: str) -> int:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}",
        headers={"Authorization": f"Bearer {web_token}"},
    )
    assert res.status_code == 200, res.text
    return int(res.json()["source_change_id"])


def _latest_tx_change_payload(TS, ledger_internal_id: str, tx_id: str) -> dict:
    with TS() as db:
        row = db.scalar(
            select(SyncChange)
            .where(
                SyncChange.ledger_id == ledger_internal_id,
                SyncChange.entity_type == "transaction",
                SyncChange.entity_sync_id == tx_id,
            )
            .order_by(SyncChange.change_id.desc())
        )
        assert row is not None, "no SyncChange for tx"
        return row.payload_json


# --------------------------------------------------------------------------- #
# Test 1: web create with flags → response + SyncChange + projection           #
# --------------------------------------------------------------------------- #

def test_web_create_tx_with_exclude_flags() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "exflag_c1@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "EX_C1")

        web_token = _login_web(client, "exflag_c1@example.com")["access_token"]
        web_hdr = {"Authorization": f"Bearer {web_token}", "X-Device-ID": "pytest-web"}
        base = _base_change_id(client, web_token, "EX_C1")

        create_res = client.post(
            "/api/v1/write/ledgers/EX_C1/transactions",
            headers=web_hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 42.0,
                "happened_at": _iso(),
                "exclude_from_stats": True,
                "exclude_from_budget": False,
            },
        )
        assert create_res.status_code == 200, create_res.text
        tx_id = create_res.json()["entity_id"]

        # read 端点能读回这两个布尔
        list_res = client.get(
            "/api/v1/read/ledgers/EX_C1/transactions",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert list_res.status_code == 200, list_res.text
        items = [it for it in list_res.json() if it["id"] == tx_id]
        assert len(items) == 1, f"tx {tx_id} not in read list"
        item = items[0]
        assert item["exclude_from_stats"] is True
        assert item["exclude_from_budget"] is False

        # SyncChange payload 用 camelCase
        lid = _ledger_internal_id(TS, "EX_C1")
        payload = _latest_tx_change_payload(TS, lid, tx_id)
        assert payload.get("excludeFromStats") is True, payload
        assert payload.get("excludeFromBudget") is False, payload

        # projection 落库
        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == lid,
                    ReadTxProjection.sync_id == tx_id,
                )
            )
            assert row is not None
            assert row.exclude_from_stats is True
            assert row.exclude_from_budget is False
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Test 2: web update toggles one flag, other unchanged                          #
# --------------------------------------------------------------------------- #

def test_web_update_tx_toggle_flag() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "exflag_u1@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "EX_U1")

        web_token = _login_web(client, "exflag_u1@example.com")["access_token"]
        web_hdr = {"Authorization": f"Bearer {web_token}", "X-Device-ID": "pytest-web"}
        base = _base_change_id(client, web_token, "EX_U1")

        # create with both False
        create_res = client.post(
            "/api/v1/write/ledgers/EX_U1/transactions",
            headers=web_hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 10.0,
                "happened_at": _iso(),
                "exclude_from_stats": False,
                "exclude_from_budget": True,
            },
        )
        assert create_res.status_code == 200, create_res.text
        tx_id = create_res.json()["entity_id"]
        new_base = int(create_res.json()["new_change_id"])

        # update: flip exclude_from_stats only, omit exclude_from_budget
        upd_res = client.patch(
            f"/api/v1/write/ledgers/EX_U1/transactions/{tx_id}",
            headers=web_hdr,
            json={
                "base_change_id": new_base,
                "exclude_from_stats": True,
            },
        )
        assert upd_res.status_code == 200, upd_res.text

        lid = _ledger_internal_id(TS, "EX_U1")
        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == lid,
                    ReadTxProjection.sync_id == tx_id,
                )
            )
            assert row is not None
            assert row.exclude_from_stats is True, "stats flag should be toggled on"
            assert row.exclude_from_budget is True, "budget flag must remain unchanged"

        payload = _latest_tx_change_payload(TS, lid, tx_id)
        assert payload.get("excludeFromStats") is True, payload
        assert payload.get("excludeFromBudget") is True, payload
    finally:
        app.dependency_overrides.clear()
