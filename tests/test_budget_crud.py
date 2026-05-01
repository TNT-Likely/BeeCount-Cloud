"""Web budget CRUD round-trips through projection + emits SyncChange with
`ledgerSyncId` so mobile pull (`_applyBudgetChange`) can resolve local ledger.

Regression for: web 创建/更新预算后,mobile pull 因 payload 缺 ledgerSyncId
直接 skip,app 永远刷不出来 — 同时 web 自身刷新时读 projection 看不到 update。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


def _make_client() -> TestClient:
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
    return TestClient(app)


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


def _latest_change_id(client: TestClient, token: str, ledger_id: str) -> int:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return int(res.json()["source_change_id"])


def test_create_budget_then_update_amount_persists_and_carries_ledger_sync_id() -> None:
    client = _make_client()
    try:
        owner = _register(client, "b@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_BUDGET"
        _seed_ledger(client, app_token, device, ledger_id)

        # 切到 web token 做 budget CRUD(write 路由要求 web scope)
        web = _login_web(client, "b@example.com")
        token = web["access_token"]

        # 创建总预算
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/budgets",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "type": "total",
                "amount": 3000,
                "period": "monthly",
                "start_day": 1,
            },
        )
        assert res.status_code == 200, res.text
        commit = res.json()
        new_change_id = int(commit["new_change_id"])

        # 读列表确认 amount=3000
        res = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/budgets",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        budgets = res.json()
        assert len(budgets) == 1
        assert budgets[0]["type"] == "total"
        assert budgets[0]["amount"] == 3000.0
        budget_id = budgets[0]["id"]

        # 更新金额到 5000
        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/budgets/{budget_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "amount": 5000},
        )
        assert res.status_code == 200, res.text

        # 读列表应该看到 5000(回归: 之前 web 刷新看不到 update 是因为 projection 没刷)
        res = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/budgets",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        budgets = res.json()
        assert len(budgets) == 1
        assert budgets[0]["amount"] == 5000.0

        # mobile 拉 SyncChange 列表,验证 budget change 的 payload 带 ledgerSyncId
        # (没带的话 _applyBudgetChange 会因 localLedgerId==null 直接 skip)
        # 用 app token 访问 /sync/pull(只接受 app_write 或 web_read scope)
        res = client.get(
            "/api/v1/sync/pull",
            headers={"Authorization": f"Bearer {app_token}"},
            params={"since": new_change_id - 1, "device_id": device, "limit": 100},
        )
        assert res.status_code == 200, res.text
        changes = res.json().get("changes") or []
        budget_changes = [
            c for c in changes if c.get("entity_type") == "budget" and c.get("action") == "upsert"
        ]
        assert budget_changes, "expected at least one budget upsert change"
        for c in budget_changes:
            payload = c.get("payload") or {}
            assert payload.get("ledgerSyncId") == ledger_id, (
                f"budget change missing ledgerSyncId: {payload}"
            )
    finally:
        app.dependency_overrides.clear()


def test_create_category_budget_persists_category_and_ledger_sync_id() -> None:
    client = _make_client()
    try:
        owner = _register(client, "bc@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_CAT_BUDGET"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "bc@example.com")
        token = web["access_token"]

        # 先创建一个支出分类
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/categories",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "name": "餐饮",
                "kind": "expense",
                "level": 1,
            },
        )
        assert res.status_code == 200, res.text

        # 拿到 category sync_id
        res = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/categories",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        cats = res.json()
        cat_id = next(c["id"] for c in cats if c["name"] == "餐饮")

        # 创建分类预算
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/budgets",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "type": "category",
                "category_id": cat_id,
                "amount": 800,
            },
        )
        assert res.status_code == 200, res.text

        # 读列表确认 categoryId 正确
        res = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/budgets",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        budgets = res.json()
        assert any(b["type"] == "category" and b["category_id"] == cat_id for b in budgets)
    finally:
        app.dependency_overrides.clear()


def test_total_budget_duplicate_blocked() -> None:
    client = _make_client()
    try:
        owner = _register(client, "bd@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DUP_BUDGET"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "bd@example.com")
        token = web["access_token"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/budgets",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "type": "total", "amount": 1000},
        )
        assert res.status_code == 200, res.text

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/budgets",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "type": "total", "amount": 2000},
        )
        # total 唯一,第二条应当 400
        assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.clear()
