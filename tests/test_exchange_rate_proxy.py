"""汇率代理契约:命中缓存不打上游/上游全挂回 stale/无缓存 503/开关关闭 404。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.config import get_settings
from src.database import Base, get_db
from src.main import app
from src.models import ExchangeRateCache
from src.services.exchange_rate import fetcher


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
    """注册 + app 登录(写数据用)+ web 登录(读端点用 web_read scope)。"""
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    # web 登录拿 web_read scope(conftest ALLOW_APP_RW_SCOPES=false,读端点必须 web)
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
    return r.json()["access_token"]


@pytest.fixture(autouse=True)
def _clear_locks():
    """每个用例前后都清空 fetcher._locks,保证并发锁状态不跨用例污染。"""
    fetcher._locks.clear()
    yield
    fetcher._locks.clear()


@pytest.fixture()
def fake_upstream(monkeypatch):
    calls = {"n": 0}

    async def _fake(base: str):
        calls["n"] += 1
        return "2026-06-10", "fawazahmed0", {"USD": "0.1477", "JPY": "21.65"}

    monkeypatch.setattr(fetcher, "fetch_upstream", _fake)
    return calls


def test_proxy_fetch_then_cache_hit(fake_upstream):
    """GET base=cny → 200, base 归一 CNY, rates 字符串;第二次请求 calls 仍为 1(缓存命中)。"""
    client, _ = _make_client()
    try:
        token = _login_web(client, "proxy1@t.com")
        hdr = {"Authorization": f"Bearer {token}"}

        r = client.get("/api/v1/read/exchange-rates", headers=hdr, params={"base": "cny"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["base"] == "CNY"
        assert body["stale"] is False
        assert isinstance(body["rates"], dict)
        assert body["rates"]["USD"] == "0.1477"
        assert fake_upstream["n"] == 1

        # 第二次 — TTL 内,走缓存,上游不应再被调用
        r2 = client.get("/api/v1/read/exchange-rates", headers=hdr, params={"base": "CNY"})
        assert r2.status_code == 200, r2.text
        assert fake_upstream["n"] == 1  # 仍然是 1,缓存命中
    finally:
        app.dependency_overrides.clear()


def test_proxy_upstream_down_serves_stale(monkeypatch):
    """先成功一次 → 把缓存行 fetched_at 改到 48h 前 → 上游 mock 抛错 → 200 + stale=true + 旧 rates。"""
    client, TS = _make_client()
    try:
        token = _login_web(client, "proxy2@t.com")
        hdr = {"Authorization": f"Bearer {token}"}

        # 第一次:上游成功,写入缓存
        async def _ok(base: str):
            return "2026-06-08", "fawazahmed0", {"USD": "0.1400", "JPY": "20.00"}

        monkeypatch.setattr(fetcher, "fetch_upstream", _ok)
        r = client.get("/api/v1/read/exchange-rates", headers=hdr, params={"base": "CNY"})
        assert r.status_code == 200, r.text

        # 把缓存行的 fetched_at 改到 48 小时前(TTL 过期)
        stale_time = datetime.now(timezone.utc) - timedelta(hours=48)
        with TS() as db:
            row = db.get(ExchangeRateCache, "CNY")
            assert row is not None
            row.fetched_at = stale_time
            db.commit()

        # 上游挂掉
        async def _fail(base: str):
            raise RuntimeError("upstream down")

        monkeypatch.setattr(fetcher, "fetch_upstream", _fail)

        r2 = client.get("/api/v1/read/exchange-rates", headers=hdr, params={"base": "CNY"})
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["stale"] is True
        assert body["rates"]["USD"] == "0.1400"  # 旧 rates 仍返回
    finally:
        app.dependency_overrides.clear()


def test_proxy_no_cache_upstream_down_503(monkeypatch):
    """上游 mock 抛错且无缓存 → 503。"""
    client, _ = _make_client()
    try:
        token = _login_web(client, "proxy3@t.com")
        hdr = {"Authorization": f"Bearer {token}"}

        async def _fail(base: str):
            raise RuntimeError("all upstreams failed")

        monkeypatch.setattr(fetcher, "fetch_upstream", _fail)

        r = client.get("/api/v1/read/exchange-rates", headers=hdr, params={"base": "CNY"})
        assert r.status_code == 503, r.text
    finally:
        app.dependency_overrides.clear()


def test_proxy_disabled_404(monkeypatch):
    """settings.exchange_rate_proxy_enabled=False → 404。"""
    client, _ = _make_client()
    try:
        token = _login_web(client, "proxy4@t.com")
        hdr = {"Authorization": f"Bearer {token}"}

        # get_settings() 带 lru_cache,直接 setattr 已有实例属性即可
        monkeypatch.setattr(get_settings(), "exchange_rate_proxy_enabled", False)

        r = client.get("/api/v1/read/exchange-rates", headers=hdr, params={"base": "CNY"})
        assert r.status_code == 404, r.text
    finally:
        app.dependency_overrides.clear()
        # 还原(避免影响其他测试)
        monkeypatch.setattr(get_settings(), "exchange_rate_proxy_enabled", True)


def test_proxy_invalid_base_422_before_fetcher(monkeypatch):
    """非法 base 必须在进 fetcher 前被 pattern 校验拦下 → 422,
    不进 _locks、不拼上游、不落缓存(安全评审 P0)。"""
    client, _ = _make_client()

    # 上游 mock 设成一调用就爆,证明非法 base 根本没走到 fetcher
    async def _boom(base):  # pragma: no cover - 不应被调用
        raise AssertionError(f"fetcher 不该被非法 base 触达: {base!r}")

    monkeypatch.setattr(fetcher, "fetch_upstream", _boom)
    locks_before = len(fetcher._locks)
    try:
        token = _login_web(client, "proxy5@t.com")
        hdr = {"Authorization": f"Bearer {token}"}
        for bad in ("../x", "us d", "ZZZZZZZZZ", "x", "C/N", "A" * 500):
            r = client.get("/api/v1/read/exchange-rates", headers=hdr, params={"base": bad})
            assert r.status_code == 422, f"base={bad!r} 应 422,实际 {r.status_code}: {r.text}"
        # _locks 不因垃圾 base 增长
        assert len(fetcher._locks) == locks_before
    finally:
        app.dependency_overrides.clear()
