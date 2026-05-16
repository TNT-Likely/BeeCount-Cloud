"""End-to-end shared-ledger flows.

覆盖 Sprint 1 综合场景:Owner / Editor 在同一账本的 push / pull / read 交错,
确保数据可见性 + 权限边界 + 跨账本隔离都正确。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


def _make_client() -> TestClient:
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
    return TestClient(app)


def _register(client: TestClient, email: str, client_type: str = "web") -> dict:
    """注册:web token 同时拥有 web:read/write 用于 REST,sync push 用 app token。

    Phase 1 中 mobile 客户端走 sync/push(SCOPE_APP_WRITE),Web 走 REST
    (SCOPE_WEB_WRITE)。conftest 把 ALLOW_APP_RW_SCOPES 关掉了,所以 app token
    不能调 /write/ledgers。共享账本场景需要两种入口都试,所以默认拿 web token
    (调 invites/members/create_ledger),再单独 register app device 用于 sync。
    """
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": "test",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    body["user_id"] = body["user"]["id"]
    return body


def _login_app_device(client: TestClient, email: str) -> dict:
    """再额外用 app device 登录一次,拿一个 SCOPE_APP_WRITE 的 token 做 sync push。"""
    res = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "123456",
            "client_type": "app",
            "device_name": "pytest-app",
            "platform": "test",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _setup_shared_ledger(client: TestClient) -> tuple[dict, dict, str]:
    """注册 owner + editor,owner 建账本,邀请 editor 加入。

    返回的 owner / editor dict 里都额外塞一个 ``app_access_token`` + ``app_device_id``
    便于后续测试做 sync push(REST 操作用 access_token,sync 用 app_access_token)。
    """
    owner = _register(client, "owner@example.com")
    owner_app = _login_app_device(client, "owner@example.com")
    owner["app_access_token"] = owner_app["access_token"]
    owner["app_device_id"] = owner_app["device_id"]

    editor = _register(client, "editor@example.com")
    editor_app = _login_app_device(client, "editor@example.com")
    editor["app_access_token"] = editor_app["access_token"]
    editor["app_device_id"] = editor_app["device_id"]

    res = client.post(
        "/api/v1/write/ledgers",
        headers={"Authorization": f"Bearer {owner['access_token']}"},
        json={"ledger_id": "shared", "ledger_name": "Shared", "currency": "CNY"},
    )
    assert res.status_code == 200, res.text
    ledger_id = res.json()["ledger_id"]

    res = client.post(
        f"/api/v1/ledgers/{ledger_id}/invites",
        headers={"Authorization": f"Bearer {owner['access_token']}"},
        json={"role": "editor", "expires_in_hours": 24},
    )
    code = res.json()["code"]
    res = client.post(
        f"/api/v1/invites/{code}/accept",
        headers={"Authorization": f"Bearer {editor['access_token']}"},
    )
    assert res.status_code == 200, res.text

    return owner, editor, ledger_id


def _push_tx(client: TestClient, token: str, device_id: str, ledger_id: str, tx_sync_id: str, *, amount: float = 38.0) -> int:
    now = datetime.now(timezone.utc).isoformat()
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": "transaction",
                    "entity_sync_id": tx_sync_id,
                    "action": "upsert",
                    "payload": {
                        "syncId": tx_sync_id,
                        "type": "expense",
                        "amount": amount,
                        "happenedAt": now,
                        "note": "lunch",
                    },
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["server_cursor"]


# ---------------------------------------------------------------------------

def test_editor_can_push_transaction_owner_can_pull() -> None:
    client = _make_client()
    try:
        owner, editor, ledger_id = _setup_shared_ledger(client)

        # Editor push 一笔(app token 通 sync push)
        _push_tx(client, editor["app_access_token"], editor["app_device_id"], ledger_id, "tx_editor_1")

        # Owner pull(app token)→ 应该看到 editor 写的 tx
        res = client.get(
            "/api/v1/sync/pull?since=0",
            headers={"Authorization": f"Bearer {owner['app_access_token']}"},
        )
        assert res.status_code == 200, res.text
        changes = res.json()["changes"]
        tx_changes = [c for c in changes if c["entity_type"] == "transaction"]
        assert any(c["entity_sync_id"] == "tx_editor_1" for c in tx_changes)
    finally:
        app.dependency_overrides.clear()


def test_editor_can_read_owner_workspace_transactions() -> None:
    client = _make_client()
    try:
        owner, editor, ledger_id = _setup_shared_ledger(client)
        _push_tx(client, owner["app_access_token"], owner["app_device_id"], ledger_id, "tx_owner_1", amount=88.0)

        # Editor 读 owner 写的 tx(走 read/workspace 路径)
        res = client.get(
            f"/api/v1/read/workspace/transactions?ledger_id={ledger_id}",
            headers={"Authorization": f"Bearer {editor['access_token']}"},
        )
        assert res.status_code == 200, res.text
        items = res.json()["items"]
        # workspace 端点字段是 `id`(对应 ReadTxProjection.sync_id);不是 sync_id
        assert any(it.get("id") == "tx_owner_1" for it in items), items
    finally:
        app.dependency_overrides.clear()


def test_editor_cannot_push_category() -> None:
    """Editor 不能往共享账本推 category(user-global,Owner-only)。"""
    client = _make_client()
    try:
        _owner, editor, ledger_id = _setup_shared_ledger(client)
        now = datetime.now(timezone.utc).isoformat()
        res = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {editor['app_access_token']}"},
            json={
                "device_id": editor["app_device_id"],
                "changes": [
                    {
                        "ledger_id": ledger_id,
                        "entity_type": "category",
                        "entity_sync_id": "cat_evil",
                        "action": "upsert",
                        "payload": {
                            "syncId": "cat_evil",
                            "name": "Editor's new category",
                            "kind": "expense",
                        },
                        "updated_at": now,
                    }
                ],
            },
        )
        # Phase 1:Editor 不能写 user-global 资源,sync push 拒绝 → 404
        assert res.status_code == 404, res.text
    finally:
        app.dependency_overrides.clear()


def test_editor_cannot_delete_ledger() -> None:
    client = _make_client()
    try:
        _owner, editor, ledger_id = _setup_shared_ledger(client)
        res = client.delete(
            f"/api/v1/write/ledgers/{ledger_id}",
            headers={"Authorization": f"Bearer {editor['access_token']}"},
        )
        assert res.status_code == 404, res.text
    finally:
        app.dependency_overrides.clear()


def test_outsider_sees_no_data_from_shared_ledger() -> None:
    client = _make_client()
    try:
        owner, editor, ledger_id = _setup_shared_ledger(client)
        outsider = _register(client, "outsider@example.com")
        outsider_app = _login_app_device(client, "outsider@example.com")
        _push_tx(client, owner["app_access_token"], owner["app_device_id"], ledger_id, "tx_secret")

        # Outsider workspace 看不到
        res = client.get(
            f"/api/v1/read/workspace/transactions?ledger_id={ledger_id}",
            headers={"Authorization": f"Bearer {outsider['access_token']}"},
        )
        assert res.status_code == 200, res.text
        items = res.json()["items"]
        assert items == []

        # Outsider sync pull 也不该看到
        res = client.get(
            "/api/v1/sync/pull?since=0",
            headers={"Authorization": f"Bearer {outsider_app['access_token']}"},
        )
        assert res.status_code == 200, res.text
        changes = res.json()["changes"]
        assert all(c["ledger_id"] != ledger_id for c in changes), changes

        # 不影响 editor / owner 各自正常工作
        assert editor["user_id"] != outsider["user_id"]
    finally:
        app.dependency_overrides.clear()


def test_kicked_editor_loses_access() -> None:
    client = _make_client()
    try:
        owner, editor, ledger_id = _setup_shared_ledger(client)
        _push_tx(client, editor["app_access_token"], editor["app_device_id"], ledger_id, "tx_pre_kick")

        # Owner 踢 editor
        res = client.delete(
            f"/api/v1/ledgers/{ledger_id}/members/{editor['user_id']}",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
        )
        assert res.status_code == 204, res.text

        # Editor 现在不能 read
        res = client.get(
            f"/api/v1/read/workspace/transactions?ledger_id={ledger_id}",
            headers={"Authorization": f"Bearer {editor['access_token']}"},
        )
        assert res.status_code == 200
        assert res.json()["items"] == []

        # Editor 不能再 push tx 到原 ledger;不过 sync push 的"非 member auto-create
        # 同 external_id ledger"机制会给 editor 创建一个自己 own 的同名 ledger,
        # 这是 by design 的隔离机制(两个用户可以独立各自一个 'shared'),只要
        # owner 视图不被污染就行。
        now = datetime.now(timezone.utc).isoformat()
        res = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {editor['app_access_token']}"},
            json={
                "device_id": editor["app_device_id"],
                "changes": [
                    {
                        "ledger_id": ledger_id,
                        "entity_type": "transaction",
                        "entity_sync_id": "tx_post_kick",
                        "action": "upsert",
                        "payload": {
                            "syncId": "tx_post_kick",
                            "type": "expense",
                            "amount": 1,
                            "happenedAt": now,
                        },
                        "updated_at": now,
                    }
                ],
            },
        )
        # Editor 被踢后 push 该 ledger_id:服务端的 access 查不到他,自动 fall through
        # 到 auto-create 分支,创建一个新的 owner ledger(同 external_id 不冲突 —
        # (user_id, external_id) 复合 unique)。这是 by design 的语义,但要确认
        # 不会污染原 ledger:owner 的视图里只有 tx_pre_kick,看不到 tx_post_kick。
        assert res.status_code == 200, res.text

        res = client.get(
            f"/api/v1/read/workspace/transactions?ledger_id={ledger_id}",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
        )
        items = res.json()["items"]
        sync_ids = {it.get("id") for it in items}
        assert "tx_pre_kick" in sync_ids
        assert "tx_post_kick" not in sync_ids, items
    finally:
        app.dependency_overrides.clear()


def test_transfer_swaps_who_can_delete() -> None:
    """transfer 后,新 owner 可删账本;旧 owner 不行。"""
    client = _make_client()
    try:
        owner, editor, ledger_id = _setup_shared_ledger(client)

        res = client.post(
            f"/api/v1/ledgers/{ledger_id}/transfer",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
            json={"new_owner_user_id": editor["user_id"]},
        )
        assert res.status_code == 200, res.text

        # 旧 owner 现在是 editor — 不能删
        res = client.delete(
            f"/api/v1/write/ledgers/{ledger_id}",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
        )
        assert res.status_code == 404, res.text

        # 新 owner 可删
        res = client.delete(
            f"/api/v1/write/ledgers/{ledger_id}",
            headers={"Authorization": f"Bearer {editor['access_token']}"},
        )
        assert res.status_code == 200, res.text
    finally:
        app.dependency_overrides.clear()
