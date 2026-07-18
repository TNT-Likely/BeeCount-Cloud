"""账户隐藏(issue #240,.docs/account-archive)Cloud 端契约 —— push/merge/full 侧:

- user-global account push payload 带 `hidden`(camelCase 同名,与 App
  serializeAccount 对齐)→ 落 user_account_projection.hidden 列(alembic 0019)
- partial-update(后续 push 只改 name、不带 hidden 键)时保持原值 —— **这是
  CLAUDE.md L74-80 要求的新增字段 merge 契约测试**,防止 hidden 被静默冲成 false
- `/sync/full` 从 projection 懒构建(snapshot_builder.build)必须带 hidden,
  否则重装 / 新设备丢隐藏标记(纯 payload 透传方案最大的坑,03-tech-design §二)
- pull 原样透传 hidden(mobile ↔ mobile 增量同步靠这个存活)

读端点(schema/D1 反向断言)由 Task 3 追加。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import User, UserAccountProjection


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


def _login(client, email, *, device_id="d1", client_type="app"):
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Pa$$word1!"},
    )
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": device_id,
            "client_type": client_type,
            "device_name": "pytest",
            "platform": "test",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d1", action="upsert"):
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
        json={"device_id": device_id, "changes": [body]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _account_row(TS, email, sync_id) -> UserAccountProjection:
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        assert user_id is not None
        row = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == user_id,
                UserAccountProjection.sync_id == sync_id,
            )
        )
        assert row is not None
        db.expunge(row)
        return row


# --------------------------------------------------------------------------- #
# Task 2: merge / upsert / snapshot                                           #
# --------------------------------------------------------------------------- #


def test_push_account_persists_hidden():
    """push 的 account payload 带 hidden=True → 落 user_account_projection.hidden。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "hidden1@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-1",
              {"syncId": "acc-1", "name": "旧卡", "type": "cash", "currency": "CNY",
               "hidden": True})

        row = _account_row(TS, "hidden1@t.com", "acc-1")
        assert row.hidden is True
    finally:
        app.dependency_overrides.clear()


def test_push_account_defaults_hidden_false_when_absent():
    """旧 App / 新建账户不带 hidden 键 → 落库默认 False(不是 NULL,列 NOT NULL)。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "hidden2@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-2",
              {"syncId": "acc-2", "name": "新卡", "type": "cash", "currency": "CNY"})

        row = _account_row(TS, "hidden2@t.com", "acc-2")
        assert row.hidden is False
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_account_partial_update_keeps_hidden():
    """**merge 契约(CLAUDE.md L74-80 硬门槛)**:先 push 一条 hidden=True 的账户,
    再 push 一条只改 name、不带 hidden 键的 partial update —— hidden 必须仍为
    True,不能被 partial update 静默冲成 False。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "hidden3@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-3",
              {"syncId": "acc-3", "name": "旧卡", "type": "cash", "currency": "CNY",
               "hidden": True})
        # partial update:只带 name,不带 hidden 键
        _push(client, hdr, "lg1", "account", "acc-3",
              {"syncId": "acc-3", "name": "旧卡改名"})

        row = _account_row(TS, "hidden3@t.com", "acc-3")
        assert row.name == "旧卡改名"
        assert row.hidden is True, "partial update 不带 hidden 键时不能冲掉已有的隐藏标记"
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_account_partial_update_can_explicitly_unhide():
    """反面情形:partial update **显式**带 hidden=False(用户主动取消隐藏)时,
    必须正常覆盖为 False —— 跟"缺键保留"的契约不冲突(False 不是 None/缺失)。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "hidden4@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-4",
              {"syncId": "acc-4", "name": "旧卡", "type": "cash", "currency": "CNY",
               "hidden": True})
        _push(client, hdr, "lg1", "account", "acc-4",
              {"syncId": "acc-4", "name": "旧卡", "hidden": False})

        row = _account_row(TS, "hidden4@t.com", "acc-4")
        assert row.hidden is False
    finally:
        app.dependency_overrides.clear()


def test_pull_roundtrips_hidden():
    """push 带 hidden=True → pull 原样回传(mobile ↔ mobile 增量同步靠 payload 透传存活)。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "hidden5@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-5",
              {"syncId": "acc-5", "name": "旧卡", "type": "cash", "currency": "CNY",
               "hidden": True})

        # 不带 device_id 查询参数:server 只在 device_id 存在时才过滤掉该 device
        # 自己推的 change(pull.py:78-79),省了另外注册一台设备的麻烦。
        r = client.get("/api/v1/sync/pull?since=0", headers=hdr)
        assert r.status_code == 200, r.text
        changes = [c for c in r.json()["changes"] if c["entity_sync_id"] == "acc-5"]
        assert len(changes) == 1
        assert changes[0]["payload"]["hidden"] is True
    finally:
        app.dependency_overrides.clear()


def test_snapshot_builder_keeps_account_hidden():
    """/sync/full 的 snapshot 从 projection 懒构建(snapshot_builder.build):
    account item 必须带 hidden(无条件输出,与 App serializeAccount 无条件发对齐),
    否则重装 / 新设备首次全量同步后隐藏标记丢失(03-tech-design-cloud.md §二 (B))。"""
    from src.models import Ledger
    from src.snapshot_builder import build

    client, TS = _make_client()
    try:
        tok = _login(client, "hidden6@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"})
        _push(client, hdr, "lg1", "account", "acc-hidden",
              {"syncId": "acc-hidden", "name": "隐藏卡", "type": "cash",
               "currency": "CNY", "hidden": True})
        _push(client, hdr, "lg1", "account", "acc-visible",
              {"syncId": "acc-visible", "name": "正常卡", "type": "cash",
               "currency": "CNY"})

        with TS() as db:
            ledger = db.scalar(select(Ledger).where(Ledger.external_id == "lg1"))
            snap = build(db, ledger)
        by_id = {acc["syncId"]: acc for acc in snap["accounts"]}
        assert by_id["acc-hidden"]["hidden"] is True
        assert by_id["acc-visible"]["hidden"] is False
    finally:
        app.dependency_overrides.clear()
