"""Ledger soft-delete: mobile push or web DELETE should both hide the ledger
from subsequent reads, while preserving the underlying SyncChange history for
audit. Other users' ledgers with the same external_id must be unaffected."""

from __future__ import annotations

import os
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


def _register(client: TestClient, email: str, client_type: str = "app") -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": client_type,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _login_web(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/login",
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


def _seed_snapshot(client: TestClient, token: str, device_id: str, ledger_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    content = (
        f'{{"ledgerName":"{ledger_id}","currency":"CNY","count":0,'
        '"items":[],"accounts":[],"categories":[],"tags":[]}'
    )
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": "ledger_snapshot",
                    "entity_sync_id": ledger_id,
                    "action": "upsert",
                    "payload": {"content": content},
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def test_mobile_push_delete_hides_ledger_from_reads() -> None:
    client = _make_client()
    try:
        owner = _register(client, "o@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        _seed_snapshot(client, app_token, device, "L1")

        # Confirm ledger visible pre-delete.
        r = client.get("/api/v1/sync/ledgers", headers={"Authorization": f"Bearer {app_token}"})
        assert [lg["ledger_id"] for lg in r.json()] == ["L1"]

        # Mobile pushes a ledger_snapshot delete tombstone.
        now = datetime.now(timezone.utc).isoformat()
        res = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {app_token}"},
            json={
                "device_id": device,
                "changes": [
                    {
                        "ledger_id": "L1",
                        "entity_type": "ledger_snapshot",
                        "entity_sync_id": "L1",
                        "action": "delete",
                        "payload": {},
                        "updated_at": now,
                    }
                ],
            },
        )
        assert res.status_code == 200, res.text

        # /sync/ledgers now skips it.
        r = client.get("/api/v1/sync/ledgers", headers={"Authorization": f"Bearer {app_token}"})
        assert r.json() == []

        # web /read/ledgers also skips it; /read/ledgers/L1 → 404.
        web = _login_web(client, "o@example.com")
        web_token = web["access_token"]
        r = client.get("/api/v1/read/ledgers", headers={"Authorization": f"Bearer {web_token}"})
        assert r.json() == []
        r = client.get("/api/v1/read/ledgers/L1", headers={"Authorization": f"Bearer {web_token}"})
        assert r.status_code == 404

        # History is preserved (tombstone + original upsert rows both present).
        from sqlalchemy import func, select

        from src.models import SyncChange

        override = app.dependency_overrides[get_db]
        db = next(override())
        try:
            count = db.scalar(
                select(func.count(SyncChange.change_id)).where(
                    SyncChange.entity_type == "ledger_snapshot"
                )
            )
            assert count >= 2, count
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_web_delete_ledger_endpoint() -> None:
    client = _make_client()
    try:
        owner = _register(client, "o@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        _seed_snapshot(client, app_token, device, "L1")

        web = _login_web(client, "o@example.com")
        web_token = web["access_token"]

        # Web deletes via DELETE /write/ledgers/L1
        r = client.delete(
            "/api/v1/write/ledgers/L1",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert r.status_code == 200, r.text
        meta = r.json()
        assert meta["ledger_id"] == "L1"
        assert meta["new_change_id"] > 0

        # Subsequently not visible to either mobile or web.
        r = client.get("/api/v1/sync/ledgers", headers={"Authorization": f"Bearer {app_token}"})
        assert r.json() == []
        r = client.get("/api/v1/read/ledgers", headers={"Authorization": f"Bearer {web_token}"})
        assert r.json() == []
    finally:
        app.dependency_overrides.clear()


def test_web_delete_prunes_sync_history_and_attachments(tmp_path) -> None:
    """Web DELETE /write/ledgers/{id} 必须真清干净:
       - sync_changes 历史只剩 tombstone(其它 entity events 全删掉)
       - attachment_files 行删干净 + 物理文件 unlink
       - read_*_projection 清零

    跟"软删除"(只 truncate projection 留全部历史)的旧策略对比,这是
    更激进的清理 — 用户在 web UI 主动点删除时假定"真的不要了"。
    """
    from src.config import get_settings

    # 把附件 storage dir 改成 tmp,测试结束自动清理
    settings = get_settings()
    original_dir = settings.attachment_storage_dir
    settings.attachment_storage_dir = str(tmp_path / "attachments")
    os.makedirs(settings.attachment_storage_dir, exist_ok=True)

    client = _make_client()
    try:
        owner = _register(client, "owner@delete.com")
        app_token, device = owner["access_token"], owner["device_id"]
        _seed_snapshot(client, app_token, device, "LDEL")

        # 通过 mobile push 写一笔交易,顺手种几条 sync_changes 进去 — 不然
        # 测不到 sync_history 清理。
        now = datetime.now(timezone.utc).isoformat()
        client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {app_token}"},
            json={
                "device_id": device,
                "changes": [
                    {
                        "ledger_id": "LDEL",
                        "entity_type": "transaction",
                        "entity_sync_id": "tx1",
                        "action": "upsert",
                        "payload": {
                            "syncId": "tx1",
                            "type": "expense",
                            "amount": 10,
                            "happenedAt": now,
                        },
                        "updated_at": now,
                    }
                ],
            },
        )

        # 拿到 ledger 内部 id 直接造一条 attachment_files 行 + 物理文件
        from sqlalchemy import select as sql_select
        from src.database import get_db
        from src.models import AttachmentFile, Ledger, SyncChange

        get_db_override = app.dependency_overrides[get_db]
        db = next(get_db_override())
        try:
            ledger_row = db.scalar(sql_select(Ledger).where(Ledger.external_id == "LDEL"))
            assert ledger_row is not None
            ledger_internal = ledger_row.id

            att_path = os.path.join(settings.attachment_storage_dir, ledger_internal, "att.bin")
            os.makedirs(os.path.dirname(att_path), exist_ok=True)
            with open(att_path, "wb") as f:
                f.write(b"fake-image-data")

            att_row = AttachmentFile(
                ledger_id=ledger_internal,
                user_id=ledger_row.user_id,
                sha256="a" * 64,
                size_bytes=15,
                mime_type="image/png",
                file_name="att.bin",
                storage_path=att_path,
                attachment_kind="transaction",
            )
            db.add(att_row)
            db.commit()
            att_id = att_row.id

            # 也造一条 user-global 的 category_icon(ledger_id=NULL),
            # 验证 web delete 不会误删跨账本的图标。
            global_att_path = os.path.join(
                settings.attachment_storage_dir, "global", "icon.png",
            )
            os.makedirs(os.path.dirname(global_att_path), exist_ok=True)
            with open(global_att_path, "wb") as f:
                f.write(b"icon")
            global_att = AttachmentFile(
                ledger_id=None,
                user_id=ledger_row.user_id,
                sha256="b" * 64,
                size_bytes=4,
                mime_type="image/png",
                file_name="icon.png",
                storage_path=global_att_path,
                attachment_kind="category_icon",
            )
            db.add(global_att)
            db.commit()
            global_att_id = global_att.id
        finally:
            db.close()

        # 确认前置:文件存在 + sync_changes 至少有 ledger_snapshot upsert + tx upsert
        assert os.path.exists(att_path)
        db = next(get_db_override())
        try:
            from sqlalchemy import func as sql_func
            pre_count = db.scalar(
                sql_select(sql_func.count(SyncChange.change_id)).where(
                    SyncChange.ledger_id == ledger_internal,
                )
            )
            assert pre_count >= 2, f"expected ≥2 changes pre-delete, got {pre_count}"
        finally:
            db.close()

        # Web 删除
        web = _login_web(client, "owner@delete.com")
        r = client.delete(
            "/api/v1/write/ledgers/LDEL",
            headers={"Authorization": f"Bearer {web['access_token']}"},
        )
        assert r.status_code == 200, r.text
        new_change_id = r.json()["new_change_id"]

        # 验证:sync_changes 只剩 tombstone(change_id == new_change_id)
        db = next(get_db_override())
        try:
            remaining = list(
                db.execute(
                    sql_select(SyncChange.change_id, SyncChange.action, SyncChange.entity_type)
                    .where(SyncChange.ledger_id == ledger_internal)
                ).all()
            )
            assert len(remaining) == 1, f"expected only tombstone, got {remaining}"
            assert remaining[0].change_id == new_change_id
            assert remaining[0].action == "delete"
            assert remaining[0].entity_type == "ledger_snapshot"

            # attachment_files 行被删
            att_remaining = db.get(AttachmentFile, att_id)
            assert att_remaining is None

            # user-global category_icon 不受影响
            global_remaining = db.get(AttachmentFile, global_att_id)
            assert global_remaining is not None
        finally:
            db.close()

        # 物理文件 unlink
        assert not os.path.exists(att_path), "ledger attachment file should be unlinked"
        # 全局图标文件保留
        assert os.path.exists(global_att_path), "global category icon should NOT be unlinked"
    finally:
        app.dependency_overrides.clear()
        settings.attachment_storage_dir = original_dir


def test_soft_delete_does_not_affect_other_users() -> None:
    client = _make_client()
    try:
        a = _register(client, "a@example.com")
        b = _register(client, "b@example.com")
        _seed_snapshot(client, a["access_token"], a["device_id"], "shared-name")
        _seed_snapshot(client, b["access_token"], b["device_id"], "shared-name")

        # A deletes its own ledger.
        now = datetime.now(timezone.utc).isoformat()
        client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {a['access_token']}"},
            json={
                "device_id": a["device_id"],
                "changes": [
                    {
                        "ledger_id": "shared-name",
                        "entity_type": "ledger_snapshot",
                        "entity_sync_id": "shared-name",
                        "action": "delete",
                        "payload": {},
                        "updated_at": now,
                    }
                ],
            },
        )

        # A's list: empty. B's list: still visible.
        ra = client.get(
            "/api/v1/sync/ledgers",
            headers={"Authorization": f"Bearer {a['access_token']}"},
        )
        rb = client.get(
            "/api/v1/sync/ledgers",
            headers={"Authorization": f"Bearer {b['access_token']}"},
        )
        assert ra.json() == []
        assert [lg["ledger_id"] for lg in rb.json()] == ["shared-name"]
    finally:
        app.dependency_overrides.clear()
