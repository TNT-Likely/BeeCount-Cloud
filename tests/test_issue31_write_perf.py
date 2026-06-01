"""issue #31 A1b / A3 回归测试 —— 单笔 create fast-path + 批量 MCP 写工具。

  - A1b: POST /write/ledgers/{id}/transactions 走 _commit_create_tx_fast,
         产出的 tx 可读、sync_change 可 pull(语义与旧的全量 build 路径一致)。
  - A3:  write_tools.create_transactions 批量工具:分块走 /transactions/batch、
         注入 MCP 标签、多账本拒绝瞎猜、未知分类清晰报错。
  (A2 的 threadpool 化由全量 write 测试覆盖 —— 所有 POST/PATCH/DELETE 现在都
   经过 run_in_threadpool。)
"""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import _mcp_internal_client
from src.database import Base, get_db
from src.main import app
from src.mcp.tools import read_tools, write_tools
from src.models import User


def _make_client_and_engine(monkeypatch):
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
    monkeypatch.setattr(read_tools, "SessionLocal", TS)
    monkeypatch.setattr(write_tools, "SessionLocal", TS)
    return TestClient(app), TS


def _register(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
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


def _make_ledger(client: TestClient, token: str, name: str) -> str:
    res = client.post(
        "/api/v1/write/ledgers",
        json={"ledger_name": name, "currency": "CNY"},
        headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
    )
    assert res.status_code == 200, res.text
    return res.json()["entity_id"]


def _fetch_user(TS, email: str) -> User:
    with TS() as db:
        u = db.scalar(select(User).where(User.email == email))
        assert u is not None
        db.expunge(u)
        return u


def _run_async(coro):
    """驱动 async MCP write tool。internal ASGI client 是进程级单例 —— 每次重置,
    绑到本次 asyncio.run 的 loop;跑完再清掉,别泄漏给后续测试。"""
    _mcp_internal_client._client = None
    try:
        return asyncio.run(coro)
    finally:
        _mcp_internal_client._client = None


# --------------------------------------------------------------------------
# A1b — 单笔 create fast-path
# --------------------------------------------------------------------------


def test_a1b_create_fast_path_roundtrip(monkeypatch) -> None:
    client, _TS = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, "a1b@example.com")
        token = u["access_token"]
        led = _make_ledger(client, token, "L")
        hdr = {"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"}

        res = client.post(
            f"/api/v1/write/ledgers/{led}/transactions",
            json={
                "base_change_id": 0,
                "tx_type": "expense",
                "amount": 12.5,
                "happened_at": "2026-05-01T00:00:00+00:00",
                "note": "coffee",
            },
            headers=hdr,
        )
        assert res.status_code == 200, res.text
        sync_id = res.json()["entity_id"]
        assert sync_id

        # 读回:fast-path 写的 tx 字段正确
        r2 = client.get(f"/api/v1/read/ledgers/{led}/transactions", headers=hdr)
        assert r2.status_code == 200, r2.text
        rows = [it for it in r2.json() if it["id"] == sync_id]
        assert len(rows) == 1, r2.json()
        assert rows[0]["amount"] == 12.5
        assert rows[0]["note"] == "coffee"
        assert rows[0]["tx_type"] == "expense"
    finally:
        app.dependency_overrides.clear()


def test_a1b_create_fast_path_emits_pullable_change(monkeypatch) -> None:
    client, _TS = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, "a1bpull@example.com")
        token, device = u["access_token"], u["device_id"]
        led = _make_ledger(client, token, "L")
        hdr = {"Authorization": f"Bearer {token}", "X-Device-ID": "other-device"}

        res = client.post(
            f"/api/v1/write/ledgers/{led}/transactions",
            json={"base_change_id": 0, "tx_type": "income", "amount": 9,
                  "happened_at": "2026-05-02T00:00:00+00:00"},
            headers=hdr,
        )
        assert res.status_code == 200, res.text
        sync_id = res.json()["entity_id"]

        # fast-path 必须 emit 一条可增量 pull 的 transaction upsert change
        r = client.get("/api/v1/sync/pull?since=0&limit=500",
                       headers={"Authorization": f"Bearer {token}", "X-Device-ID": device})
        assert r.status_code == 200, r.text
        tx_changes = [
            c for c in r.json()["changes"]
            if c.get("entity_type") == "transaction"
            and c.get("entity_sync_id") == sync_id
            and c.get("action") == "upsert"
        ]
        assert len(tx_changes) == 1, r.json()
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# A3 — 批量 MCP 写工具
# --------------------------------------------------------------------------


def test_a3_batch_endpoint_one_commit(monkeypatch) -> None:
    """A3 的落地端点 /transactions/batch:50 笔一次 commit + A2 threadpool 化后仍
    正确。(create_transactions 这个 MCP 包装器对它的 self-call 走 app-scope token,
    而测试套件 conftest 把 ALLOW_APP_RW_SCOPES 设为 false,self-call 必 403 —— 跟
    test_mcp_tools 只测 read 工具同因;故这里直接用 web token 测端点本体。)"""
    client, _TS = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, "batch@example.com")
        token = u["access_token"]
        led = _make_ledger(client, token, "B")
        hdr = {"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"}

        txns = [
            {"tx_type": "expense", "amount": 1.0 + i,
             "happened_at": "2026-05-01T00:00:00+00:00", "tags": ["MCP"]}
            for i in range(50)
        ]
        res = client.post(
            f"/api/v1/write/ledgers/{led}/transactions/batch",
            json={"base_change_id": 0, "transactions": txns, "auto_ai_tag": False},
            headers=hdr,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert len(body["created_sync_ids"]) == 50
        assert body["new_change_id"] > 0  # 单次 commit 的 change_id

        # 全部落库 + MCP tag 实体建出来了
        r = client.get(f"/api/v1/read/ledgers/{led}/transactions?limit=200", headers=hdr)
        assert r.status_code == 200, r.text
        assert len(r.json()) == 50
        tags = client.get(f"/api/v1/read/ledgers/{led}/tags", headers=hdr).json()
        assert any(t["name"] == "MCP" for t in tags), tags
    finally:
        app.dependency_overrides.clear()


def test_a3_bulk_refuses_multi_ledger_without_id(monkeypatch) -> None:
    client, TS = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, "a3multi@example.com")
        token = u["access_token"]
        _make_ledger(client, token, "One")
        _make_ledger(client, token, "Two")
        user = _fetch_user(TS, "a3multi@example.com")

        result = _run_async(
            write_tools.create_transactions(
                user,
                transactions=[{"amount": 5, "tx_type": "expense", "happened_at": "2026-05-01"}],
                ledger_id=None,
            )
        )
        # B5:多账本不指定 → 拒绝瞎猜,返回候选(不写入)
        assert result["status"] == "ledger_required", result
        assert len(result["candidates"]) == 2
    finally:
        app.dependency_overrides.clear()


def test_a3_bulk_unknown_category_errors(monkeypatch) -> None:
    import pytest

    client, TS = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, "a3cat@example.com")
        token = u["access_token"]
        led = _make_ledger(client, token, "L")
        user = _fetch_user(TS, "a3cat@example.com")

        with pytest.raises(ValueError, match="Unknown categories"):
            _run_async(
                write_tools.create_transactions(
                    user,
                    transactions=[{"amount": 5, "category": "NoSuchCat",
                                   "happened_at": "2026-05-01"}],
                    ledger_id=led,
                )
            )
    finally:
        app.dependency_overrides.clear()
