"""报销模块测试(0042 补充:流水变更 → 报销同步)。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

NOW = datetime.now(timezone.utc)


def _make_client() -> TestClient:
    from src.database import Base, get_db
    from src.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
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
    return TestClient(app)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "123456", "client_type": "web",
              "device_name": "t", "platform": "web"},
    )
    assert res.status_code in (200, 201), res.text
    return res.json()


def _ledger(client: TestClient, token: str, ledger_id: str) -> None:
    res = client.post(
        "/api/v1/write/ledgers", headers=_auth(token),
        json={"ledger_id": ledger_id, "ledger_name": ledger_id, "currency": "CNY",
              "month_start_day": 1},
    )
    assert res.status_code == 200, res.text


def _account(client: TestClient, token: str, ledger_id: str, name: str) -> str:
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/accounts", headers=_auth(token),
        json={"base_change_id": 0, "name": name},
    )
    assert res.status_code == 200, res.text
    return res.json()["entity_id"]


def _tx(client: TestClient, token: str, ledger_id: str, amount: float, note: str) -> str:
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions", headers=_auth(token),
        json={"base_change_id": 0, "tx_type": "expense", "amount": amount,
              "happened_at": NOW.isoformat(), "category_name": "差旅", "note": note},
    )
    assert res.status_code in (200, 201), res.text
    return res.json()["entity_id"]


def _company(client: TestClient, token: str, name: str) -> str:
    res = client.post(
        "/api/v1/companies", headers=_auth(token), json={"name": name},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_reimbursement_item_syncs_when_tx_amount_changes() -> None:
    """0042:编辑流水的金额 → 报销项金额同步。

    报销项(ReimbursementItem)的 amount 是快照;原始账单金额改了,报销项
    必须跟着改,否则「待报销」列表显示的金额与原始账单对不上。
    """
    client = _make_client()
    user = _register(client, "rbsync@example.com")
    token = user["access_token"]
    _ledger(client, token, "LRB")
    acc = _account(client, token, "LRB", "现金")

    tx_id = _tx(client, token, "LRB", 100.0, "打车费")
    company_id = _company(client, token, "测试公司")
    res = client.post(
        "/api/v1/reimbursements/items", headers=_auth(token),
        json={"ledger_id": "LRB", "tx_sync_id": tx_id, "amount": 100.0,
              "happened_at": NOW.isoformat(), "account_id": acc, "account_name": "现金",
              "company_id": company_id},
    )
    assert res.status_code == 201, res.text
    item = res.json()
    assert item["amount"] == 100.0

    # 交易侧把流水改成 180 → 报销项金额同步
    res = client.patch(
        f"/api/v1/write/ledgers/LRB/transactions/{tx_id}",
        headers=_auth(token),
        json={"base_change_id": 0, "amount": 180.0},
    )
    assert res.status_code == 200, res.text

    res = client.get(f"/api/v1/reimbursements/items/by-tx/{tx_id}", headers=_auth(token))
    assert res.status_code == 200, res.text
    assert res.json()["amount"] == 180.0, res.json()
