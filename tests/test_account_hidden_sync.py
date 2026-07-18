"""账户隐藏(issue #240,.docs/account-archive)Cloud 端契约 —— push/merge/full 侧:

- user-global account push payload 带 `hidden`(camelCase 同名,与 App
  serializeAccount 对齐)→ 落 user_account_projection.hidden 列(alembic 0019)
- partial-update(后续 push 只改 name、不带 hidden 键)时保持原值 —— **这是
  CLAUDE.md L74-80 要求的新增字段 merge 契约测试**,防止 hidden 被静默冲成 false
- `/sync/full` 从 projection 懒构建(snapshot_builder.build)必须带 hidden,
  否则重装 / 新设备丢隐藏标记(纯 payload 透传方案最大的坑,03-tech-design §二)
- pull 原样透传 hidden(mobile ↔ mobile 增量同步靠这个存活)
- Read/Workspace 响应补 hidden 字段(Web 可见)+ D1 反向断言:隐藏账户仍进
  WorkspaceAccountOut 的 balance/income/expense 聚合,服务端不做任何统计过滤
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import SyncChange, User, UserAccountProjection


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


# --------------------------------------------------------------------------- #
# Task 3: schemas + 读端点(Web 可见)+ 无统计过滤反向断言(D1)                   #
# --------------------------------------------------------------------------- #


def test_read_ledger_accounts_expose_hidden():
    """/read/ledgers/{id}/accounts(ReadAccountOut)带 hidden 字段。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "hidden7@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "hidden7@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc-r1",
              {"syncId": "acc-r1", "name": "隐藏卡", "type": "cash",
               "currency": "CNY", "hidden": True}, device_id="d-app")

        r = client.get("/api/v1/read/ledgers/lg1/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        acc = next(x for x in r.json() if x["id"] == "acc-r1")
        assert acc["hidden"] is True
    finally:
        app.dependency_overrides.clear()


def test_workspace_accounts_expose_hidden_and_do_not_filter_stats():
    """/read/workspace/accounts(WorkspaceAccountOut)带 hidden 字段;**D1 反向断言**:
    隐藏账户仍然正常进 balance/income/expense 聚合,服务端不因 hidden 加任何
    统计过滤 —— 隐藏只影响前端选择器/列表呈现。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "hidden8@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "hidden8@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc-w1",
              {"syncId": "acc-w1", "name": "隐藏卡", "type": "cash",
               "currency": "CNY", "initialBalance": 100.0, "hidden": True},
              device_id="d-app")
        _push(client, hdr_app, "lg1", "transaction", "tx-w1",
              {"syncId": "tx-w1", "type": "expense", "amount": 30.0,
               "happenedAt": _iso(), "accountId": "acc-w1", "accountName": "隐藏卡"},
              device_id="d-app")

        r = client.get("/api/v1/read/workspace/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        acc = next(x for x in r.json() if x["id"] == "acc-w1")
        assert acc["hidden"] is True
        # D1:隐藏账户仍照常计入统计 —— balance = initialBalance(100) - expense(30) = 70
        assert acc["expense_total"] == 30.0
        assert acc["balance"] == 70.0
        assert acc["tx_count"] == 1
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Task 4: Web 写端点(可编辑 hidden)                                            #
# --------------------------------------------------------------------------- #


def _latest_account_change(TS, email: str, sync_id: str) -> SyncChange:
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        assert user_id is not None
        row = db.scalar(
            select(SyncChange)
            .where(
                SyncChange.user_id == user_id,
                SyncChange.entity_type == "account",
                SyncChange.entity_sync_id == sync_id,
            )
            .order_by(SyncChange.change_id.desc())
        )
        assert row is not None, f"no SyncChange for account {sync_id}"
        return row


def test_web_create_account_with_hidden():
    """POST /write/ledgers/{id}/accounts 带 hidden=true → 落投影 + 读端点可见。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "hiddenw1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "hiddenw1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgw1", "ledger", "lgw1",
              {"syncId": "lgw1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgw1/accounts",
            headers=hdr_web,
            json={"base_change_id": 0, "name": "隐藏卡", "hidden": True},
        )
        assert r.status_code == 200, r.text
        account_id = r.json()["entity_id"]
        assert account_id

        row = _account_row(TS, "hiddenw1@t.com", account_id)
        assert row.hidden is True

        r2 = client.get("/api/v1/read/ledgers/lgw1/accounts", headers=hdr_web)
        assert r2.status_code == 200, r2.text
        acc = next(x for x in r2.json() if x["id"] == account_id)
        assert acc["hidden"] is True
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_toggles_hidden_and_syncs_to_app():
    """PATCH hidden=true → snapshot 变更落投影 + 反向发射 SyncChange(payload 带
    hidden,键名跟 App serializeAccount 对齐)→ App 端正常 /sync/pull 收敛。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "hiddenw2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "hiddenw2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgw2", "ledger", "lgw2",
              {"syncId": "lgw2", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgw2", "account", "acc-w2",
              {"syncId": "acc-w2", "name": "旧卡", "type": "cash", "currency": "CNY"},
              device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgw2/accounts/acc-w2",
            headers=hdr_web,
            json={"base_change_id": 0, "hidden": True},
        )
        assert r.status_code == 200, r.text

        row = _account_row(TS, "hiddenw2@t.com", "acc-w2")
        assert row.hidden is True

        # 反向 SyncChange:payload 带 hidden=True(camelCase 同名,跟 App
        # serializeAccount 对齐)。
        change = _latest_account_change(TS, "hiddenw2@t.com", "acc-w2")
        assert change.payload_json.get("hidden") is True, change.payload_json

        # App 端走正常 /sync/pull 收敛(增量同步,不带 device_id 免得被自己
        # 的 push 过滤掉)。
        r3 = client.get("/api/v1/sync/pull?since=0", headers=hdr_app)
        assert r3.status_code == 200, r3.text
        changes = [
            c for c in r3.json()["changes"]
            if c["entity_type"] == "account" and c["entity_sync_id"] == "acc-w2"
        ]
        assert len(changes) >= 1
        assert changes[-1]["payload"]["hidden"] is True
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_hidden_omitted_keeps_existing():
    """web PATCH 只改 note、不带 hidden 键 → hidden 保持不变。跟 mobile push 的
    merge 缺键保留契约一致,web 写路径也不能把已有隐藏标记冲成 false。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "hiddenw3@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "hiddenw3@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgw3", "ledger", "lgw3",
              {"syncId": "lgw3", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgw3", "account", "acc-w3",
              {"syncId": "acc-w3", "name": "旧卡", "type": "cash", "currency": "CNY",
               "hidden": True}, device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgw3/accounts/acc-w3",
            headers=hdr_web,
            json={"base_change_id": 0, "note": "备注"},
        )
        assert r.status_code == 200, r.text

        row = _account_row(TS, "hiddenw3@t.com", "acc-w3")
        assert row.hidden is True, "web update 不带 hidden 键时不能冲掉已有隐藏标记"
        assert row.note == "备注"
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_hidden_can_explicitly_restore():
    """web PATCH 显式带 hidden=false(用户点「恢复」)→ 正常覆盖为 False。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "hiddenw5@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "hiddenw5@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgw5", "ledger", "lgw5",
              {"syncId": "lgw5", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgw5", "account", "acc-w5",
              {"syncId": "acc-w5", "name": "旧卡", "type": "cash", "currency": "CNY",
               "hidden": True}, device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgw5/accounts/acc-w5",
            headers=hdr_web,
            json={"base_change_id": 0, "hidden": False},
        )
        assert r.status_code == 200, r.text

        row = _account_row(TS, "hiddenw5@t.com", "acc-w5")
        assert row.hidden is False

        change = _latest_account_change(TS, "hiddenw5@t.com", "acc-w5")
        assert change.payload_json.get("hidden") is False, change.payload_json
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_hidden_idempotent_replay():
    """同一 Idempotency-Key 重放 PATCH → 不重复推进 change_id,直接 replay 原响应
    (往返 + 幂等契约)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "hiddenw4@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "hiddenw4@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {
            "Authorization": f"Bearer {web_tok}",
            "X-Device-ID": "d-web",
            "Idempotency-Key": "idem-hidden-1",
        }

        _push(client, hdr_app, "lgw4", "ledger", "lgw4",
              {"syncId": "lgw4", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgw4", "account", "acc-w4",
              {"syncId": "acc-w4", "name": "旧卡", "type": "cash", "currency": "CNY"},
              device_id="d-app")

        body = {"base_change_id": 0, "hidden": True}
        r1 = client.patch(
            "/api/v1/write/ledgers/lgw4/accounts/acc-w4", headers=hdr_web, json=body,
        )
        assert r1.status_code == 200, r1.text
        first_change_id = r1.json()["new_change_id"]
        assert r1.json()["idempotency_replayed"] is False

        r2 = client.patch(
            "/api/v1/write/ledgers/lgw4/accounts/acc-w4", headers=hdr_web, json=body,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["new_change_id"] == first_change_id
        assert r2.json()["idempotency_replayed"] is True

        row = _account_row(TS, "hiddenw4@t.com", "acc-w4")
        assert row.hidden is True
    finally:
        app.dependency_overrides.clear()
