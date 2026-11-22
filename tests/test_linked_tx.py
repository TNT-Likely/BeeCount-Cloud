"""关联流水摘要的统一下发与渲染数据源(0046)。

凡是「业务实体引用了某条交易流水」的地方,详情页都要能拿到**可读**的
摘要(备注 / 金额 / 时间 / 分类 / 账户),而不是只有裸 syncId。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)


def _make_client() -> TestClient:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "123456", "client_type": "web",
              "device_name": "pytest", "platform": "web"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _ledger(client: TestClient, token: str, ledger_id: str) -> None:
    res = client.post(
        f"/api/v1/write/ledgers", headers=_auth(token),
        json={"ledger_id": ledger_id, "ledger_name": ledger_id, "currency": "CNY",
              "month_start_day": 1},
    )
    assert res.status_code == 200, res.text


def _tx(client: TestClient, token: str, ledger_id: str, amount: float, note: str) -> str:
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions", headers=_auth(token),
        json={
            "base_change_id": 0, "tx_type": "expense", "amount": amount,
            "happened_at": NOW.isoformat(), "category_name": "购物", "note": note,
        },
    )
    assert res.status_code in (200, 201), res.text
    entity_id = res.json().get("entity_id")
    assert entity_id, res.text
    return entity_id


def test_linked_tx_details_expose_readable_info() -> None:
    """0046:借款 / 物品 / 定期存款 / 报销 都要返回关联流水的可读摘要。"""
    client = _make_client()
    user = _register(client, "ltx@example.com")
    token = user["access_token"]
    _ledger(client, token, "LTX")

    tx_id = _tx(client, token, "LTX", 168.0, "台灯")

    # --- 物品 ---
    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={"ledger_id": "LTX", "name": "台灯", "price": 0.0,
              "link_mode": "existing", "tx_sync_ids": [tx_id]},
    )
    assert res.status_code == 201, res.text
    item = res.json()
    details = item["tx_sync_details"]
    assert len(details) == 1, details
    assert details[0]["note"] == "台灯", details[0]
    assert details[0]["amount"] == 168.0, details[0]
    assert details[0]["category_name"] == "购物", details[0]
    assert details[0]["happened_at"], details[0]

    # --- 借款(需选账户才生成关联流水)---
    res = client.post(
        "/api/v1/contacts", headers=_auth(token), json={"name": "张三"},
    )
    assert res.status_code in (200, 201), res.text
    cid = res.json()["id"]
    res = client.post(
        "/api/v1/write/ledgers/LTX/accounts", headers=_auth(token),
        json={"base_change_id": 0, "name": "现金"},
    )
    assert res.status_code == 200, res.text
    acc = {"id": res.json()["entity_id"], "name": "现金"}
    res = client.post(
        "/api/v1/loans", headers=_auth(token),
        json={"ledger_id": "LTX", "direction": "lend", "amount": 168.0,
              "contact_id": cid, "account_id": acc["id"], "account_name": acc["name"],
              "happened_at": NOW.isoformat(), "note": "借出"},
    )
    assert res.status_code in (200, 201), res.text
    loan = res.json()
    assert loan["tx_sync_id"], loan
    assert loan["tx_sync_details"], loan
    assert loan["tx_sync_details"][0]["amount"] == 168.0, loan["tx_sync_details"]

    # 列表也要带摘要(详情弹窗用的是列表数据)
    res = client.get("/api/v1/loans?ledger_id=LTX", headers=_auth(token))
    assert res.status_code == 200, res.text
    listed = res.json()[0]
    assert listed["tx_sync_details"], listed

    # --- 还款也带摘要 ---
    res = client.post(
        f"/api/v1/loans/{loan['id']}/repayments", headers=_auth(token),
        json={"amount": 168.0, "repaid_at": NOW.isoformat(),
              "account_id": acc["id"], "account_name": acc["name"]},
    )
    assert res.status_code in (200, 201), res.text
    res = client.get(f"/api/v1/loans/{loan['id']}/repayments", headers=_auth(token))
    reps = res.json()
    assert reps, reps
    assert reps[0]["tx_sync_details"], reps[0]


def test_linked_tx_details_mark_deleted_flow() -> None:
    """0046:关联的流水被删除后,摘要仍列出该条并标注「已删除」(不静默消失)。"""
    client = _make_client()
    user = _register(client, "ltxdel@example.com")
    token = user["access_token"]
    _ledger(client, token, "LTXD")

    tx_id = _tx(client, token, "LTXD", 50.0, "会被删掉的流水")
    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={"ledger_id": "LTXD", "name": "旧物", "price": 0.0,
              "link_mode": "existing", "tx_sync_ids": [tx_id]},
    )
    assert res.status_code == 201, res.text
    item = res.json()

    # 删掉这笔流水
    res = client.get(f"/api/v1/read/ledgers/LTXD", headers=_auth(token))
    base = max(0, int(res.json().get("last_change_id") or 0) - 1)
    res = client.request(
        "DELETE",
        f"/api/v1/write/ledgers/LTXD/transactions/{tx_id}", headers=_auth(token),
        json={"base_change_id": base},
    )
    assert res.status_code in (200, 204), res.text

    res = client.get(f"/api/v1/items/{item['id']}", headers=_auth(token))
    assert res.status_code == 200, res.text
    details = res.json()["tx_sync_details"]
    assert len(details) == 1, details
    # 流水已删 → happened_at 为空(前端据此标注「已删除」),但条目仍在
    assert details[0]["sync_id"] == tx_id
    assert not details[0]["happened_at"], details[0]
