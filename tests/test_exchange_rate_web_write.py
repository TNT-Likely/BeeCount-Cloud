"""Web 写 override 契约:PUT 按币对 upsert(复用既有 sync_id)+落 SyncChange(scope=user);
DELETE 移除 projection 并发 delete change;App 经 pull 可收敛。"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import SyncChange, UserExchangeRateProjection


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


def _login_web(client, email):
    """注册 + web 登录(ALLOW_APP_RW_SCOPES=false 时写端点必须 web scope)。"""
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
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
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_put_creates_then_updates_same_pair():
    """首次 PUT 创建 projection 行;同币对(大小写不敏感)再次 PUT 应复用同一 sync_id,
    projection 只有 1 行且 rate 更新;每次 PUT 各落一条 SyncChange。"""
    client, TS = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_login_web(client, 'wr1@t.com')}"}

        r1 = client.put(
            "/api/v1/write/exchange-rate-overrides",
            headers=hdr,
            json={"base_currency": "cny", "quote_currency": "usd", "rate": "7.20"},
        )
        assert r1.status_code == 200, r1.text
        sync_id = r1.json()["sync_id"]
        assert r1.json()["base_currency"] == "CNY"
        assert r1.json()["quote_currency"] == "USD"
        assert r1.json()["rate"] == "7.20"

        r2 = client.put(
            "/api/v1/write/exchange-rate-overrides",
            headers=hdr,
            json={"base_currency": "CNY", "quote_currency": "USD", "rate": "7.50"},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["sync_id"] == sync_id  # 同币对复用 sync_id

        with TS() as db:
            rows = db.scalars(
                select(UserExchangeRateProjection).where(
                    UserExchangeRateProjection.sync_id == sync_id
                )
            ).all()
            assert len(rows) == 1 and rows[0].rate == "7.50"

            changes = db.scalars(
                select(SyncChange).where(
                    SyncChange.entity_type == "exchange_rate_override"
                )
            ).all()
            assert len(changes) == 2
            assert all(c.scope == "user" and c.ledger_id is None for c in changes)
    finally:
        app.dependency_overrides.clear()


def test_delete_removes_and_emits_change():
    """PUT 一条 JPY/USD → DELETE(query 参数 base_currency/quote_currency) → 200;
    projection 行消失;SyncChange 出现 action=='delete'。"""
    client, TS = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_login_web(client, 'wr2@t.com')}"}

        r = client.put(
            "/api/v1/write/exchange-rate-overrides",
            headers=hdr,
            json={"base_currency": "JPY", "quote_currency": "CNY", "rate": "0.046"},
        )
        assert r.status_code == 200, r.text
        sync_id = r.json()["sync_id"]

        rd = client.delete(
            "/api/v1/write/exchange-rate-overrides",
            headers=hdr,
            params={"base_currency": "JPY", "quote_currency": "CNY"},
        )
        assert rd.status_code == 200, rd.text
        assert rd.json()["sync_id"] == sync_id
        assert rd.json()["base_currency"] == "JPY"
        assert rd.json()["quote_currency"] == "CNY"

        with TS() as db:
            row = db.scalar(
                select(UserExchangeRateProjection).where(
                    UserExchangeRateProjection.sync_id == sync_id
                )
            )
            assert row is None, "projection 行应已删除"

            delete_change = db.scalar(
                select(SyncChange).where(
                    SyncChange.entity_type == "exchange_rate_override",
                    SyncChange.action == "delete",
                    SyncChange.entity_sync_id == sync_id,
                )
            )
            assert delete_change is not None
            assert delete_change.scope == "user"
            assert delete_change.ledger_id is None
    finally:
        app.dependency_overrides.clear()


def test_delete_missing_pair_404():
    """DELETE 不存在的币对 → 404。"""
    client, _ = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_login_web(client, 'wr3@t.com')}"}

        r = client.delete(
            "/api/v1/write/exchange-rate-overrides",
            headers=hdr,
            params={"base_currency": "EUR", "quote_currency": "CNY"},
        )
        assert r.status_code == 404, r.text
    finally:
        app.dependency_overrides.clear()


def test_put_invalid_rate_422():
    """rate 非数字 / rate 超界(0 或 1e10)/ base==quote → 各 422。"""
    client, _ = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_login_web(client, 'wr4@t.com')}"}

        # rate 非数字
        r = client.put(
            "/api/v1/write/exchange-rate-overrides",
            headers=hdr,
            json={"base_currency": "CNY", "quote_currency": "USD", "rate": "not-a-number"},
        )
        assert r.status_code == 422, f"非数字应 422,got {r.status_code}: {r.text}"

        # rate == 0(超界)
        r = client.put(
            "/api/v1/write/exchange-rate-overrides",
            headers=hdr,
            json={"base_currency": "CNY", "quote_currency": "USD", "rate": "0"},
        )
        assert r.status_code == 422, f"rate=0 应 422,got {r.status_code}: {r.text}"

        # rate >= 1e9(超界)
        r = client.put(
            "/api/v1/write/exchange-rate-overrides",
            headers=hdr,
            json={"base_currency": "CNY", "quote_currency": "USD", "rate": "1000000000"},
        )
        assert r.status_code == 422, f"rate>=1e9 应 422,got {r.status_code}: {r.text}"

        # base == quote
        r = client.put(
            "/api/v1/write/exchange-rate-overrides",
            headers=hdr,
            json={"base_currency": "CNY", "quote_currency": "CNY", "rate": "1.0"},
        )
        assert r.status_code == 422, f"base==quote 应 422,got {r.status_code}: {r.text}"

        for bad in ("inf", "nan", "-inf", "Infinity", "NaN"):
            r = client.put("/api/v1/write/exchange-rate-overrides", headers=hdr,
                           json={"base_currency": "CNY", "quote_currency": "USD", "rate": bad})
            assert r.status_code == 422, f"rate={bad} should 422"
    finally:
        app.dependency_overrides.clear()
