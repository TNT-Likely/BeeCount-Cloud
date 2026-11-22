"""用户级锁账(0067)端到端后端测试。

覆盖:配置读写/无日期开启被拒/锁定期交易增删改被拒/新日期交易仍可写/
账户初始金额冻结/解锁后全部恢复。
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app

LOCK_DATE = "2026-06-30T23:59:59+00:00"
OLD_TX = "2026-06-15T10:00:00+00:00"
NEW_TX = "2026-07-05T10:00:00+00:00"
LEDGER = "LOCKL1"


def _make_client() -> TestClient:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override():
        db = session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "app",
            "device_name": "pytest",
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


def _seed_ledger(client: TestClient, token: str, device_id: str) -> None:
    content = (
        f'{{"ledgerName":"{LEDGER}","currency":"CNY","count":0,'
        '"items":[],"accounts":[],"categories":[],"tags":[]}'
    )
    res = client.post(
        "/api/v1/sync/push",
        headers=_auth(token),
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": LEDGER,
                    "entity_type": "ledger_snapshot",
                    "entity_sync_id": LEDGER,
                    "action": "upsert",
                    "payload": {"content": content},
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _create_tx(client: TestClient, token: str, happened_at: str, note: str):
    return client.post(
        f"/api/v1/write/ledgers/{LEDGER}/transactions",
        headers=_auth(token),
        json={"base_change_id": 0, "amount": 100.0, "happened_at": happened_at, "note": note},
    )


def test_lock_settings_and_period_guard() -> None:
    client = _make_client()
    user = _register(client, "lock@example.com")
    token = user["access_token"]
    web = _login_web(client, "lock@example.com")
    wtoken = web["access_token"]
    _seed_ledger(client, token, user["device_id"])

    # 无日期开启锁账 → 400
    res = client.patch("/api/v1/profile/lock", headers=_auth(wtoken), json={"is_locked": True})
    assert res.status_code == 400, res.text

    # 建账户与两笔交易(一笔在锁定期内,一笔在锁定期之后)
    res = client.post(
        f"/api/v1/write/ledgers/{LEDGER}/accounts",
        headers=_auth(wtoken),
        json={"base_change_id": 0, "name": "Cash", "account_type": "cash", "initial_balance": 100.0},
    )
    assert res.status_code == 200, res.text
    account_id = res.json()["entity_id"]

    old = _create_tx(client, wtoken, OLD_TX, "old")
    assert old.status_code == 200, old.text
    old_id = old.json()["entity_id"]
    new = _create_tx(client, wtoken, NEW_TX, "new")
    assert new.status_code == 200, new.text
    new_id = new.json()["entity_id"]

    # 开启锁账
    res = client.patch(
        "/api/v1/profile/lock",
        headers=_auth(wtoken),
        json={"is_locked": True, "lock_date": LOCK_DATE},
    )
    assert res.status_code == 200 and res.json()["is_locked"] is True, res.text
    assert client.get("/api/v1/profile/lock", headers=_auth(wtoken)).json()["is_locked"] is True

    # 锁定期内交易:改/删被拒,新增也被拒
    res = client.patch(
        f"/api/v1/write/ledgers/{LEDGER}/transactions/{old_id}",
        headers=_auth(wtoken),
        json={"base_change_id": 0, "amount": 200.0},
    )
    assert res.status_code == 423, res.text
    res = client.request(
        "DELETE",
        f"/api/v1/write/ledgers/{LEDGER}/transactions/{old_id}",
        headers=_auth(wtoken),
        json={"base_change_id": 0},
    )
    assert res.status_code == 423, res.text
    assert _create_tx(client, wtoken, OLD_TX, "blocked").status_code == 423

    # 锁定期之后的交易仍然可写
    res = client.patch(
        f"/api/v1/write/ledgers/{LEDGER}/transactions/{new_id}",
        headers=_auth(wtoken),
        json={"base_change_id": 0, "amount": 150.0},
    )
    assert res.status_code == 200, res.text
    assert _create_tx(client, wtoken, NEW_TX, "allowed").status_code == 200
    res = client.request(
        "DELETE",
        f"/api/v1/write/ledgers/{LEDGER}/transactions/{new_id}",
        headers=_auth(wtoken),
        json={"base_change_id": 0},
    )
    assert res.status_code == 200, res.text

    # 账户初始金额冻结,但其它字段仍可改
    res = client.patch(
        f"/api/v1/write/ledgers/{LEDGER}/accounts/{account_id}",
        headers=_auth(wtoken),
        json={"base_change_id": 0, "initial_balance": 999.0},
    )
    assert res.status_code == 423, res.text
    res = client.patch(
        f"/api/v1/write/ledgers/{LEDGER}/accounts/{account_id}",
        headers=_auth(wtoken),
        json={"base_change_id": 0, "note": "still editable"},
    )
    assert res.status_code == 200, res.text

    # 解锁(日期保留) → 锁定期交易与初始金额恢复可操作
    res = client.patch("/api/v1/profile/lock", headers=_auth(wtoken), json={"is_locked": False})
    assert res.status_code == 200 and res.json()["is_locked"] is False, res.text
    assert res.json()["lock_date"] is not None

    res = client.patch(
        f"/api/v1/write/ledgers/{LEDGER}/transactions/{old_id}",
        headers=_auth(wtoken),
        json={"base_change_id": 0, "amount": 200.0},
    )
    assert res.status_code == 200, res.text
    res = client.request(
        "DELETE",
        f"/api/v1/write/ledgers/{LEDGER}/transactions/{old_id}",
        headers=_auth(wtoken),
        json={"base_change_id": 0},
    )
    assert res.status_code == 200, res.text
    res = client.patch(
        f"/api/v1/write/ledgers/{LEDGER}/accounts/{account_id}",
        headers=_auth(wtoken),
        json={"base_change_id": 0, "initial_balance": 999.0},
    )
    assert res.status_code == 200, res.text
