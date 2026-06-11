"""净值历史端点:按月返回截至月末的净资产累积序列。"""
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


def _register_and_token(client: TestClient, email: str, *, device_id: str, client_type: str) -> str:
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
    """注册一个用户,拿 app 和 web 两种 token。
    app token 用于 push 写数据(SCOPE_APP_WRITE),web token 用于读端点(SCOPE_WEB_READ)。
    """
    app_token = _register_and_token(client, email, device_id="d-app", client_type="app")
    web_token = _register_and_token(client, email, device_id="d-web", client_type="web")
    return app_token, web_token


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, scope=None, action="upsert"):
    body = {
        "ledger_id": ledger_id,
        "entity_type": entity_type,
        "entity_sync_id": sync_id,
        "action": action,
        "updated_at": _iso(),
        "payload": payload,
    }
    if scope is not None:
        body["scope"] = scope
    r = client.post(
        "/api/v1/sync/push",
        headers=hdr,
        json={"device_id": "d-app", "changes": [body]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_net_worth_history_accumulates_balance():
    """账户初始余额 1000,1 月支出 200 → 月末净值 800;3 月再支出 300 → 月末净值 500。"""
    client, _ = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "nwh1@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}

        # 推账户(user-scope,自动路由)
        _push(client, hdr_app, "lg1", "account", "acc-cash",
              {"syncId": "acc-cash", "name": "现金", "type": "cash",
               "initialBalance": 1000.0, "currency": "CNY"})
        # 推账本(建立 ledger 行,让 tx 有所属)
        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "个人账本", "currency": "CNY"})
        # 1 月支出 200
        _push(client, hdr_app, "lg1", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 200,
               "accountId": "acc-cash",
               "happenedAt": "2026-01-15T00:00:00+00:00"})
        # 3 月支出 300
        _push(client, hdr_app, "lg1", "transaction", "tx-2",
              {"syncId": "tx-2", "type": "expense", "amount": 300,
               "accountId": "acc-cash",
               "happenedAt": "2026-03-15T00:00:00+00:00"})

        r = client.get(
            "/api/v1/read/workspace/net-worth-history",
            headers=hdr_web,
            params={"scope": "all"},
        )
        assert r.status_code == 200, r.text
        series = {s["bucket"]: s["net_worth"] for s in r.json()["series"]}
        assert series["2026-01"] == 800.0
        # 2026-02 无交易,应补齐为上月末净值(存量持平)
        assert series["2026-02"] == 800.0
        assert series["2026-03"] == 500.0
        # 当前月若晚于 2026-03,也应被补齐为 500(持平 3 月末)
        now_ym = datetime.now(timezone.utc).strftime("%Y-%m")
        if now_ym > "2026-03":
            assert series[now_ym] == 500.0
    finally:
        app.dependency_overrides.clear()


def test_net_worth_history_empty():
    """用户无任何账本/交易时返回空序列。"""
    client, _ = _make_client()
    try:
        _, web_token = _two_tokens(client, "nwh2@t.com")
        hdr_web = {"Authorization": f"Bearer {web_token}"}

        r = client.get(
            "/api/v1/read/workspace/net-worth-history",
            headers=hdr_web,
            params={"scope": "all"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["series"] == []
    finally:
        app.dependency_overrides.clear()
