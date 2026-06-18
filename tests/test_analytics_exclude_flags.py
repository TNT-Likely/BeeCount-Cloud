"""收支分析端点 /read/workspace/analytics 按 exclude_from_stats 过滤(D1):

- exclude_from_stats=True 的交易不计入 income/expense 汇总(summary + series + 分类排行)
- 该标记只改"统计算不算它",不改"钱的位置";余额/净值口径在另一个端点
  (net-worth-history),本测试只锁收支汇总。

push 用 app token(SCOPE_APP_WRITE),读用 web token(SCOPE_WEB_READ),
与 test_net_worth_history.py 同套 fixture。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


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


def _register_and_token(
    client: TestClient, email: str, *, device_id: str, client_type: str
) -> str:
    client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": device_id,
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": "test",
        },
    )
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": device_id,
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": "test",
        },
    )
    return r.json()["access_token"]


def _two_tokens(client, email):
    app_token = _register_and_token(client, email, device_id="d-app", client_type="app")
    web_token = _register_and_token(client, email, device_id="d-web", client_type="web")
    return app_token, web_token


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, action="upsert"):
    body = {
        "ledger_id": ledger_id,
        "entity_type": entity_type,
        "entity_sync_id": sync_id,
        "action": action,
        "updated_at": _iso(),
        "payload": payload,
    }
    r = client.post(
        "/api/v1/sync/push",
        headers=hdr,
        json={"device_id": "d-app", "changes": [body]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_analytics_excludes_exclude_from_stats_transactions():
    """同账本两笔同周期支出:普通 100、exclude_from_stats=True 的 500;
    再加一笔 exclude_from_stats=True 的收入 300。
    expense_total 应只反映 100(非 600),income_total 应为 0。"""
    client, _ = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "excl-an@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "个人账本", "currency": "CNY"})

        # 普通支出 100 —— 应计入
        _push(client, hdr_app, "lg1", "transaction", "tx-normal",
              {"syncId": "tx-normal", "type": "expense", "amount": 100,
               "categoryName": "餐饮",
               "happenedAt": "2026-06-15T00:00:00+00:00"})
        # exclude_from_stats 支出 500 —— 不应计入
        _push(client, hdr_app, "lg1", "transaction", "tx-excluded",
              {"syncId": "tx-excluded", "type": "expense", "amount": 500,
               "categoryName": "餐饮",
               "excludeFromStats": True,
               "happenedAt": "2026-06-16T00:00:00+00:00"})
        # exclude_from_stats 收入 300 —— 不应计入
        _push(client, hdr_app, "lg1", "transaction", "tx-inc-excluded",
              {"syncId": "tx-inc-excluded", "type": "income", "amount": 300,
               "categoryName": "工资",
               "excludeFromStats": True,
               "happenedAt": "2026-06-17T00:00:00+00:00"})

        r = client.get(
            "/api/v1/read/workspace/analytics",
            headers=hdr_web,
            params={"scope": "all", "metric": "expense"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        summary = body["summary"]

        # 支出只算普通的 100,不含被标记的 500
        assert summary["expense_total"] == 100.0
        # 收入被标记笔排除,应为 0
        assert summary["income_total"] == 0.0
        # balance = income - expense = -100
        assert summary["balance"] == -100.0

        # 分类排行同样只反映普通支出
        ranks = {row["category_name"]: row["total"] for row in body["category_ranks"]}
        assert ranks.get("餐饮") == 100.0
    finally:
        app.dependency_overrides.clear()
