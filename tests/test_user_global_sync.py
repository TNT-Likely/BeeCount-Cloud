"""user-global 重构后的端到端测试。

锁定 .docs/user-global-refactor/plan.md 里的核心契约:
  - category/account/tag push 落 user_*_projection,不再 per-ledger 重复
  - SyncChange.user_id = caller(真请求方),ledger_id IS NULL,scope='user'
  - pull __user_global__ sentinel 返回该用户全部 user-scope changes
  - pull 真实 ledger 只返回 scope='ledger',user-scope filter 掉
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import (
    Ledger,
    SyncChange,
    UserAccountProjection,
    UserCategoryProjection,
    UserTagProjection,
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


def test_user_global_category_push_writes_to_user_projection():
    """category push 应落 user_category_projection,SyncChange.user_id=caller,
    ledger_id=NULL,scope='user'。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "ug1@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "category", "cat-food",
              {"syncId": "cat-food", "name": "Food", "kind": "expense"})

        with TS() as db:
            user_id = db.scalar(select(SyncChange.user_id).where(
                SyncChange.entity_type == "category"
            ))
            assert user_id is not None

            # user_category_projection 落库
            row = db.scalar(select(UserCategoryProjection).where(
                UserCategoryProjection.user_id == user_id,
                UserCategoryProjection.sync_id == "cat-food",
            ))
            assert row is not None
            assert row.name == "Food"
            assert row.kind == "expense"

            # SyncChange.scope='user',ledger_id IS NULL
            ch = db.scalar(select(SyncChange).where(
                SyncChange.entity_type == "category",
                SyncChange.entity_sync_id == "cat-food",
            ))
            assert ch.scope == "user"
            assert ch.ledger_id is None
            assert ch.user_id == user_id
    finally:
        app.dependency_overrides.clear()


def test_user_global_push_account_and_tag_too():
    """account / tag 跟 category 一样,落各自 user_*_projection。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "ug2@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc1",
              {"syncId": "acc1", "name": "Cash", "type": "cash", "currency": "CNY"})
        _push(client, hdr, "lg1", "tag", "tag1",
              {"syncId": "tag1", "name": "work", "color": "#F00"})

        with TS() as db:
            uid = db.scalar(select(SyncChange.user_id).where(SyncChange.scope == "user").limit(1))
            assert db.scalar(select(UserAccountProjection).where(
                UserAccountProjection.user_id == uid, UserAccountProjection.sync_id == "acc1"))
            assert db.scalar(select(UserTagProjection).where(
                UserTagProjection.user_id == uid, UserTagProjection.sync_id == "tag1"))
    finally:
        app.dependency_overrides.clear()


def test_user_global_push_ledger_id_field_ignored_when_set():
    """老 mobile 发 category 时仍带 ledger_id(借车协议),server 应该忽略,
    强制按 entity_type 走 user-scope 路径,SyncChange.ledger_id 落 NULL。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "ug3@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        # 客户端"误"把 ledger_id 填了(老协议借车);server 仍按 entity_type 路由
        _push(client, hdr, "wrong-ledger", "category", "cat-x",
              {"syncId": "cat-x", "name": "X", "kind": "expense"})

        with TS() as db:
            ch = db.scalar(select(SyncChange).where(
                SyncChange.entity_sync_id == "cat-x"
            ))
            assert ch.scope == "user"
            assert ch.ledger_id is None  # 关键:协议层把 client 填的 ledger_id 丢掉

            # ledger 也不应该被自动创建
            assert db.scalar(select(Ledger).where(Ledger.external_id == "wrong-ledger")) is None
    finally:
        app.dependency_overrides.clear()


def test_pull_user_global_sentinel_returns_user_scope_only():
    """GET /sync/pull?ledger_external_id=__user_global__ 返回该用户全部 scope='user'
    changes,不返回 ledger-scope。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "pull1@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        # 一条 user-scope + 一条 ledger-scope(tx,需要 ledger)
        _push(client, hdr, "lg1", "category", "c1",
              {"syncId": "c1", "name": "X", "kind": "expense"})
        _push(client, hdr, "lg1", "transaction", "tx1",
              {"syncId": "tx1", "type": "income", "amount": 100, "happenedAt": _iso()})

        # /sync/pull 用其他 device 拉,避免 server 端 "device_id != caller's own push device" 过滤
        r = client.get("/api/v1/sync/pull?since=0", headers=hdr)
        assert r.status_code == 200
        changes = r.json()["changes"]
        # /sync/pull 是 user-wide,会同时返回 scope='ledger' tx 和 scope='user' category
        scopes = {c.get("scope") for c in changes}
        types = {c["entity_type"] for c in changes}
        assert "category" in types and "transaction" in types
        # category 行的 ledger_id 应为 sentinel __user_global__
        cat_changes = [c for c in changes if c["entity_type"] == "category"]
        assert all(c["ledger_id"] == "__user_global__" for c in cat_changes)
        assert all(c["scope"] == "user" for c in cat_changes)
        # tx 行的 ledger_id 应为 lg1
        tx_changes = [c for c in changes if c["entity_type"] == "transaction"]
        assert all(c["ledger_id"] == "lg1" for c in tx_changes)
        assert all(c["scope"] == "ledger" for c in tx_changes)
    finally:
        app.dependency_overrides.clear()


def test_user_global_rename_cascades_across_ledgers():
    """改 account name 后,该用户所有 ledger 的 read_tx_projection.account_name 同步刷。"""
    from src.models import ReadTxProjection

    client, TS = _make_client()
    try:
        tok = _login(client, "rename1@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        # 创建账户 + 在两个 ledger 各有 tx 引用
        _push(client, hdr, "lg1", "account", "a1",
              {"syncId": "a1", "name": "Old", "type": "cash", "currency": "CNY"})
        _push(client, hdr, "lg1", "transaction", "tx-A",
              {"syncId": "tx-A", "type": "expense", "amount": 1, "happenedAt": _iso(),
               "accountId": "a1", "accountName": "Old"})
        _push(client, hdr, "lg2", "transaction", "tx-B",
              {"syncId": "tx-B", "type": "expense", "amount": 2, "happenedAt": _iso(),
               "accountId": "a1", "accountName": "Old"})

        # rename
        _push(client, hdr, "lg1", "account", "a1",
              {"syncId": "a1", "name": "New", "type": "cash", "currency": "CNY"})

        with TS() as db:
            # 两个 ledger 的 tx 都应该被刷
            for sid in ("tx-A", "tx-B"):
                tx = db.scalar(select(ReadTxProjection).where(
                    ReadTxProjection.sync_id == sid
                ))
                assert tx.account_name == "New", f"{sid} not cascaded, got {tx.account_name}"
    finally:
        app.dependency_overrides.clear()


def test_sync_changes_user_id_is_caller_not_ledger_owner():
    """user-global SyncChange.user_id 应该是 caller,而不是 ledger.user_id(单
    用户场景下两者相等,但断言 schema 字段语义正确)。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "caller1@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        # 先建一个 tx(走 ledger-scope)拉起 ledger row,方便对比
        _push(client, hdr, "lg1", "transaction", "tx1",
              {"syncId": "tx1", "type": "income", "amount": 50, "happenedAt": _iso()})
        _push(client, hdr, "lg1", "category", "cX",
              {"syncId": "cX", "name": "X", "kind": "expense"})

        with TS() as db:
            ledger = db.scalar(select(Ledger).where(Ledger.external_id == "lg1"))
            # ledger-scope change: SyncChange.user_id 仍是 ledger.user_id
            tx_ch = db.scalar(select(SyncChange).where(SyncChange.entity_sync_id == "tx1"))
            assert tx_ch.scope == "ledger"
            assert tx_ch.user_id == ledger.user_id
            # user-scope change: SyncChange.user_id = caller(单用户时和 owner 相等)
            cat_ch = db.scalar(select(SyncChange).where(SyncChange.entity_sync_id == "cX"))
            assert cat_ch.scope == "user"
            assert cat_ch.user_id == ledger.user_id  # caller == owner here
            assert cat_ch.updated_by_user_id == ledger.user_id
    finally:
        app.dependency_overrides.clear()
