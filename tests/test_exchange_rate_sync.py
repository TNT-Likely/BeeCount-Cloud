"""exchange_rate_override 同步契约:push 落 user projection,delete 移除,
pull __user_global__ 能带出;payload 方向 1 quote = rate base。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import (
    SyncChange,
    UserExchangeRateProjection,
)


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


def _login(client, email):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": "d1",
            "client_type": "app",
            "device_name": "pytest",
            "platform": "test",
        },
    )
    return r.json()["access_token"]


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
        json={"device_id": "d1", "changes": [body]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _payload(sync_id, base="CNY", quote="USD", rate="7.2034"):
    return {
        "syncId": sync_id, "baseCurrency": base, "quoteCurrency": quote,
        "rate": rate, "updatedAt": "2026-06-10T00:00:00+00:00",
    }


def test_override_push_writes_user_projection():
    client, TS = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_login(client, 'er1@t.com')}"}
        _push(client, hdr, "lg1", "exchange_rate_override", "rate-1",
              _payload("rate-1"), scope="user")
        with TS() as db:
            row = db.scalar(select(UserExchangeRateProjection).where(
                UserExchangeRateProjection.sync_id == "rate-1"))
            assert row is not None
            assert (row.base_currency, row.quote_currency, row.rate) == ("CNY", "USD", "7.2034")
            ch = db.scalar(select(SyncChange).where(SyncChange.entity_sync_id == "rate-1"))
            assert ch.scope == "user" and ch.ledger_id is None
    finally:
        from src.main import app
        app.dependency_overrides.clear()


def test_override_update_then_delete():
    client, TS = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_login(client, 'er2@t.com')}"}
        _push(client, hdr, "lg1", "exchange_rate_override", "rate-2",
              _payload("rate-2", rate="7.0"), scope="user")
        _push(client, hdr, "lg1", "exchange_rate_override", "rate-2",
              _payload("rate-2", rate="7.5"), scope="user")
        with TS() as db:
            row = db.scalar(select(UserExchangeRateProjection).where(
                UserExchangeRateProjection.sync_id == "rate-2"))
            assert row.rate == "7.5"
        _push(client, hdr, "lg1", "exchange_rate_override", "rate-2",
              {}, scope="user", action="delete")
        with TS() as db:
            assert db.scalar(select(UserExchangeRateProjection).where(
                UserExchangeRateProjection.sync_id == "rate-2")) is None
    finally:
        from src.main import app
        app.dependency_overrides.clear()


def test_override_in_user_global_pull():
    client, _ = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_login(client, 'er3@t.com')}"}
        _push(client, hdr, "lg1", "exchange_rate_override", "rate-3",
              _payload("rate-3"), scope="user")
        r = client.get(
            "/api/v1/sync/pull",
            headers=hdr,
            params={"since": 0},
        )
        assert r.status_code == 200, r.text
        types = [c["entity_type"] for c in r.json()["changes"]]
        assert "exchange_rate_override" in types
    finally:
        from src.main import app
        app.dependency_overrides.clear()


def test_same_pair_two_sync_ids_coexist():
    """双端离线各建同币对 → 两个 sync_id 的行都保留(server 不按币对去重,
    收敛在 App apply 端按币对完成 —— models.py docstring 的载荷性约定)。"""
    client, TS = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_login(client, 'er5@t.com')}"}
        _push(client, hdr, "lg1", "exchange_rate_override", "rate-A",
              _payload("rate-A", rate="7.1"), scope="user")
        _push(client, hdr, "lg1", "exchange_rate_override", "rate-B",
              _payload("rate-B", rate="7.3"), scope="user")
        with TS() as db:
            rows = db.scalars(select(UserExchangeRateProjection).where(
                UserExchangeRateProjection.quote_currency == "USD")).all()
            assert len(rows) == 2
            assert {r.sync_id for r in rows} == {"rate-A", "rate-B"}
    finally:
        from src.main import app
        app.dependency_overrides.clear()


def test_read_overrides_endpoint():
    client, _ = _make_client()
    try:
        # Push via app token (app_write), then re-login as web (web_read) for the same user.
        # ALLOW_APP_RW_SCOPES=false in conftest, so /read/* needs web_read scope.
        email = "er4@t.com"
        app_hdr = {"Authorization": f"Bearer {_login(client, email)}"}
        _push(client, app_hdr, "lg1", "exchange_rate_override", "rate-4",
              _payload("rate-4", quote="JPY", rate="0.048"), scope="user")
        # Same user, different device + client_type=web → gets web_read scope
        r_web = client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": "Pa$$word1!",
                "device_id": "d-web",
                "client_type": "web",
                "device_name": "pytest-web",
                "platform": "test",
            },
        )
        web_hdr = {"Authorization": f"Bearer {r_web.json()['access_token']}"}
        r = client.get("/api/v1/read/exchange-rate-overrides", headers=web_hdr)
        assert r.status_code == 200, r.text
        rows = r.json()
        assert any(x["quote_currency"] == "JPY" and x["rate"] == "0.048" for x in rows)
    finally:
        from src.main import app
        app.dependency_overrides.clear()
