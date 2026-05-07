"""Web 编辑账本名称 / 币种 后,mobile /sync/pull 应该能拉到一条
'ledger' upsert SyncChange。

回归 bug:_emit_entity_diffs 只 diff items/accounts/categories/tags/budgets,
不 diff 顶层 ledgerName/currency;另外 ledger.name / ledger.currency 是在
snapshot_builder 之前 assign 的,prev/next 都是新值,即使加了顶层 diff 也
检测不到。修复:把 assign 推迟到 mutate 内 + 显式 emit 'ledger' SyncChange。
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


def test_web_meta_update_emits_ledger_sync_change_for_mobile_pull() -> None:
    client = _make_client()
    try:
        owner = _register(client, "lm@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_META"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "lm@example.com")
        web_token = web["access_token"]

        # 改名 + 改币种
        base = _latest_change_id(client, web_token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/meta",
            headers={"Authorization": f"Bearer {web_token}"},
            json={
                "base_change_id": base,
                "ledger_name": "Family Budget",
                "currency": "USD",
            },
        )
        assert res.status_code == 200, res.text

        # mobile /sync/pull 必须看到一条 ledger upsert change
        res = client.get(
            "/api/v1/sync/pull",
            headers={"Authorization": f"Bearer {app_token}"},
            params={"since": base, "device_id": device, "limit": 100},
        )
        assert res.status_code == 200, res.text
        changes = res.json().get("changes") or []
        ledger_changes = [
            c for c in changes
            if c.get("entity_type") == "ledger" and c.get("action") == "upsert"
        ]
        assert ledger_changes, "expected at least one ledger upsert change"
        latest = ledger_changes[-1]
        payload = latest.get("payload") or {}
        assert payload.get("ledgerName") == "Family Budget"
        assert payload.get("currency") == "USD"

        # 同时:read/ledgers/{id} 应该返回新的 name/currency
        res = client.get(
            f"/api/v1/read/ledgers/{ledger_id}",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert res.status_code == 200
        # ReadLedgerDetail 不一定带 name/currency 字段,但 list 端点会;
        # 用 list 验更稳。
        res = client.get(
            "/api/v1/read/ledgers",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert res.status_code == 200
        rows = res.json()
        match = next((r for r in rows if r["ledger_id"] == ledger_id), None)
        assert match is not None
        assert match["ledger_name"] == "Family Budget"
        assert match["currency"] == "USD"
    finally:
        app.dependency_overrides.clear()
