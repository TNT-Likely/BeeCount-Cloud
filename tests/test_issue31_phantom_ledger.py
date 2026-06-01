"""issue #31 回归测试 —— 幽灵/软删账本 + MCP 写入不瞎猜账本。

覆盖修复:
  - B1: MCP `_resolve_ledger`/`list_ledgers`/`get_active_ledger` 排除软删账本
  - B2: web /write/* 写软删账本 → 404,且不"复活"其 projection
  - B3: /workspace/* 跨账本读排除软删账本(即便其 projection 残留行)
  - B5: MCP 写工具在多账本未指定时拒绝瞎猜,返回候选
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.mcp.tools import read_tools, write_tools
from src.models import Ledger, ReadTxProjection, SyncChange, User


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
    # read_tools / write_tools 直接 `with SessionLocal() as db:`,不走 dep tree。
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


def _delete_ledger(client: TestClient, token: str, ext_id: str) -> None:
    res = client.delete(
        f"/api/v1/write/ledgers/{ext_id}",
        headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
    )
    assert res.status_code == 200, res.text


def _add_tx(client: TestClient, token: str, ledger_ext: str, amount: float = 10.0):
    return client.post(
        f"/api/v1/write/ledgers/{ledger_ext}/transactions",
        json={
            "base_change_id": 0,
            "tx_type": "expense",
            "amount": amount,
            "happened_at": datetime.now(timezone.utc).isoformat(),
        },
        headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
    )


def _fetch_user(TS, email: str) -> User:
    with TS() as db:
        u = db.scalar(select(User).where(User.email == email))
        assert u is not None
        db.expunge(u)
        return u


# --------------------------------------------------------------------------
# B1 — MCP 解析/列举排除软删账本
# --------------------------------------------------------------------------


def test_b1_mcp_read_excludes_soft_deleted_ledger(monkeypatch) -> None:
    client, TS = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, "b1@example.com")
        token = u["access_token"]
        first = _make_ledger(client, token, "First")   # 最早创建
        second = _make_ledger(client, token, "Second")
        _delete_ledger(client, token, first)            # 软删最早那个(模拟幽灵默认账本)

        user = _fetch_user(TS, "b1@example.com")

        # list_ledgers 不再返回软删的 First
        names = {lg["name"] for lg in read_tools.list_ledgers(user)}
        assert names == {"Second"}, names

        # get_active_ledger 回退到 Second(此前会顽固选中最早的 First)
        active = read_tools.get_active_ledger(user)
        assert active is not None and active["name"] == "Second"

        # 显式传软删账本 id → None;传 live 账本 → 命中
        with TS() as db:
            assert read_tools._resolve_ledger(db, user.id, first) is None
            assert read_tools._resolve_ledger(db, user.id, second) is not None
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# B2 — 写软删账本被拒,且不复活 projection
# --------------------------------------------------------------------------


def test_b2_write_to_soft_deleted_ledger_404_no_resurrection(monkeypatch) -> None:
    client, TS = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, "b2@example.com")
        token = u["access_token"]
        led = _make_ledger(client, token, "Solo")
        _delete_ledger(client, token, led)

        # 往软删账本写一笔 → 404(此前会成功并"复活"projection)
        res = _add_tx(client, token, led)
        assert res.status_code == 404, res.text

        # 确认 projection 没有被复活出任何行
        with TS() as db:
            led_row = db.scalar(select(Ledger).where(Ledger.external_id == led))
            assert led_row is not None  # 软删保留壳行
            cnt = db.scalar(
                select(func.count())
                .select_from(ReadTxProjection)
                .where(ReadTxProjection.ledger_id == led_row.id)
            )
            assert cnt == 0
    finally:
        app.dependency_overrides.clear()


def test_b2_live_ledger_write_still_works(monkeypatch) -> None:
    """回归:B2 不能误伤正常账本的写入。"""
    client, TS = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, "b2ok@example.com")
        token = u["access_token"]
        led = _make_ledger(client, token, "Live")
        res = _add_tx(client, token, led, amount=12.5)
        assert res.status_code == 200, res.text
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# B3 — 跨账本/tag 读排除软删账本(即便 projection 残留复活行)
# --------------------------------------------------------------------------


def test_b3_workspace_excludes_soft_deleted_ledger_txns(monkeypatch) -> None:
    client, TS = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, "b3@example.com")
        token = u["access_token"]
        alive = _make_ledger(client, token, "Alive")
        ghost = _make_ledger(client, token, "Ghost")
        assert _add_tx(client, token, alive, amount=11.0).status_code == 200
        assert _add_tx(client, token, ghost, amount=22.0).status_code == 200

        # 直接插一条 Ghost 的 delete tombstone,模拟"复活/孤儿"存量坏数据:
        # tombstone 在(账本被判软删),但 projection 行仍残留 —— 不走
        # delete_ledger(它会 truncate projection,无法构造这个状态)。
        with TS() as db:
            ghost_row = db.scalar(select(Ledger).where(Ledger.external_id == ghost))
            db.add(
                SyncChange(
                    user_id=ghost_row.user_id,
                    ledger_id=ghost_row.id,
                    entity_type="ledger_snapshot",
                    entity_sync_id=ghost,
                    action="delete",
                    payload_json={},
                    updated_at=datetime.now(timezone.utc),
                    updated_by_device_id="pytest",
                    updated_by_user_id=ghost_row.user_id,
                )
            )
            db.commit()

        # /workspace/transactions 只应返回 Alive 的交易,Ghost 的被过滤
        res = client.get(
            "/api/v1/read/workspace/transactions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        items = res.json()["items"]
        assert {it["ledger_name"] for it in items} == {"Alive"}, items
        assert 22.0 not in {it["amount"] for it in items}
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# B5 — MCP 写工具多账本不瞎猜
# --------------------------------------------------------------------------


def test_b5_resolve_write_ledger_refuses_to_guess(monkeypatch) -> None:
    client, TS = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, "b5@example.com")
        token = u["access_token"]
        user = _fetch_user(TS, "b5@example.com")

        # 0 账本 → no_ledger
        with TS() as db:
            led, status = write_tools._resolve_write_ledger(db, user, None)
            assert led is None and status["status"] == "no_ledger"

        # 1 个 live 账本 → 自动选它
        first = _make_ledger(client, token, "Only")
        with TS() as db:
            led, status = write_tools._resolve_write_ledger(db, user, None)
            assert status is None and led is not None and led.external_id == first

        # 2 个 live 账本 + 未指定 → 拒绝瞎猜,返回候选
        second = _make_ledger(client, token, "Second")
        with TS() as db:
            led, status = write_tools._resolve_write_ledger(db, user, None)
            assert led is None
            assert status["status"] == "ledger_required"
            assert {c["id"] for c in status["candidates"]} == {first, second}

        # 显式指定但该账本已软删 → ledger_not_found
        _delete_ledger(client, token, first)
        with TS() as db:
            led, status = write_tools._resolve_write_ledger(db, user, first)
            assert led is None and status["status"] == "ledger_not_found"

        # 显式指定 live 账本 → 成功
        with TS() as db:
            led, status = write_tools._resolve_write_ledger(db, user, second)
            assert status is None and led is not None and led.external_id == second
    finally:
        app.dependency_overrides.clear()
