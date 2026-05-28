"""共享账本 Editor 删 tx 的清理路径专项单测。

锁定两个相关行为:

1. **附件 GC 按 ledger-scope** —— 共享账本里 Editor 上传的附件 `user_id = Editor`,
   但 ledger owner-scope SyncChange.user_id = Owner;用 user_id 过滤会 miss
   Editor 的附件留下孤儿。`gc_orphan_attachments_for_ledger` 用 ledger_id scope。

2. **删 entity 时压缩 upsert 历史** —— 实体彻底下线后,projection 已删,delete
   event 单独保留就够其它设备 sync;upsert event 保留是纯浪费。`_compact_entity_upsert_events`
   清掉 upsert,留 delete。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base
from src.models import (
    AttachmentFile,
    Ledger,
    ReadTxProjection,
    SyncChange,
    User,
    UserAccountProjection,
    UserCategoryProjection,
    UserTagProjection,
)
from src.projection import (
    gc_orphan_attachments_for_ledger,
    upsert_tx,
)
from src.sync_applier import (
    _compact_entity_upsert_events,
    _delete_budget,
    _delete_tx,
    _delete_user_account,
    _delete_user_category,
    _delete_user_tag,
)


def _make_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed_shared_ledger(db, *, ledger_id="L1", owner_id="owner", editor_id="editor"):
    """模拟共享账本:owner 拥有 ledger,editor 是另一个 user。"""
    db.add(User(id=owner_id, email="owner@x.com", password_hash="h"))
    db.add(User(id=editor_id, email="editor@x.com", password_hash="h"))
    db.add(
        Ledger(
            id=ledger_id,
            user_id=owner_id,
            external_id="ext",
            name="L",
            currency="CNY",
        )
    )
    db.flush()


def _make_attachment(tmp_path: Path, file_id: str, *, ledger_id: str, user_id: str):
    """attachment_files row + 物理文件,user_id 是上传者。"""
    storage_path = tmp_path / f"{file_id}.bin"
    storage_path.write_bytes(b"dummy")
    return (
        AttachmentFile(
            id=file_id,
            ledger_id=ledger_id,
            user_id=user_id,
            sha256=file_id,
            size_bytes=5,
            mime_type="image/png",
            file_name="a.png",
            storage_path=str(storage_path),
        ),
        storage_path,
    )


def _make_sync_change(
    *,
    user_id: str,
    entity_type: str,
    entity_sync_id: str,
    action: str,
    ledger_id: str | None = None,
    scope: str = "ledger",
    updated_at: datetime | None = None,
    payload: dict | None = None,
) -> SyncChange:
    return SyncChange(
        user_id=user_id,
        ledger_id=ledger_id,
        scope=scope,
        entity_type=entity_type,
        entity_sync_id=entity_sync_id,
        action=action,
        payload_json=payload or {},
        updated_at=updated_at or datetime.now(timezone.utc),
    )


# ============================================================================
# gc_orphan_attachments_for_ledger
# ============================================================================


def test_ledger_gc_cleans_editor_attachment_orphan(tmp_path):
    """共享账本:Editor 上传(attachment.user_id=Editor),Owner-scope delete 调
    ledger-scope GC → Editor 的附件被正确清掉。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_shared_ledger(db)
        att, path = _make_attachment(
            tmp_path, "f-by-editor", ledger_id="L1", user_id="editor",
        )
        db.add(att)
        db.commit()

        # ledger-scope GC,不传 user_id,只传 ledger_id
        n = gc_orphan_attachments_for_ledger(
            db, ledger_id="L1", file_ids={"f-by-editor"},
        )
        db.commit()

        assert n == 1, "Editor 上传的附件应该被清掉(老 user-scope GC 会漏)"
        assert not path.exists()
        assert db.get(AttachmentFile, "f-by-editor") is None


def test_ledger_gc_preserves_if_other_tx_in_ledger_refs(tmp_path):
    """同 ledger 下还有另一 user 的 tx projection 引用 → 保留。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_shared_ledger(db)
        att, path = _make_attachment(
            tmp_path, "f-shared", ledger_id="L1", user_id="editor",
        )
        db.add(att)
        # owner-scope projection 行还引用这个附件
        db.add(
            ReadTxProjection(
                ledger_id="L1",
                sync_id="tx-A",
                user_id="owner",
                tx_type="expense",
                amount=0.0,
                happened_at=datetime.now(timezone.utc),
                tx_index=0,
                source_change_id=1,
                attachments_json=json.dumps(
                    [{"fileName": "a.png", "cloudFileId": "f-shared"}]
                ),
            )
        )
        db.commit()

        n = gc_orphan_attachments_for_ledger(
            db, ledger_id="L1", file_ids={"f-shared"},
        )
        db.commit()

        assert n == 0
        assert path.exists()
        assert db.get(AttachmentFile, "f-shared") is not None


def test_ledger_gc_does_not_cross_ledger_boundary(tmp_path):
    """另一个 ledger 的 tx 引用同 fileId → 不影响本 ledger 的 GC。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_shared_ledger(db, ledger_id="L1")
        db.add(
            Ledger(
                id="L2", user_id="owner", external_id="ext2",
                name="L2", currency="CNY",
            )
        )
        att, path = _make_attachment(
            tmp_path, "f-l1", ledger_id="L1", user_id="editor",
        )
        db.add(att)
        # L2 里有个 tx 偶然引用了同 fileId(理论上不该发生,但 GC 逻辑应该按 ledger 隔离)
        db.add(
            ReadTxProjection(
                ledger_id="L2",
                sync_id="tx-L2",
                user_id="owner",
                tx_type="expense",
                amount=0.0,
                happened_at=datetime.now(timezone.utc),
                tx_index=0,
                source_change_id=1,
                attachments_json=json.dumps(
                    [{"fileName": "a.png", "cloudFileId": "f-l1"}]
                ),
            )
        )
        db.commit()

        # 在 L1 scope 内 GC:L1 没引用 → 清掉 L1 的 attachment_files 行
        # (L2 的 tx 引用不属于本 ledger,不阻止 L1 的清理)
        n = gc_orphan_attachments_for_ledger(
            db, ledger_id="L1", file_ids={"f-l1"},
        )
        db.commit()

        assert n == 1
        assert db.get(AttachmentFile, "f-l1") is None


# ============================================================================
# _compact_entity_upsert_events
# ============================================================================


def test_compact_removes_upserts_keeps_delete():
    """entity 删除时,清掉同 entity_sync_id 的 upsert events,保留 delete event。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_shared_ledger(db)
        # 3 个 upsert + 1 个 delete
        db.add(_make_sync_change(
            user_id="owner", entity_type="transaction", entity_sync_id="tx-X",
            action="upsert", ledger_id="L1", scope="ledger",
        ))
        db.add(_make_sync_change(
            user_id="owner", entity_type="transaction", entity_sync_id="tx-X",
            action="upsert", ledger_id="L1", scope="ledger",
        ))
        db.add(_make_sync_change(
            user_id="owner", entity_type="transaction", entity_sync_id="tx-X",
            action="upsert", ledger_id="L1", scope="ledger",
        ))
        db.add(_make_sync_change(
            user_id="owner", entity_type="transaction", entity_sync_id="tx-X",
            action="delete", ledger_id="L1", scope="ledger",
        ))
        db.commit()

        n = _compact_entity_upsert_events(
            db, user_id="owner", entity_type="transaction",
            entity_sync_id="tx-X",
        )
        db.commit()

        assert n == 3, "应该清掉 3 条 upsert"
        rows = db.scalars(
            select(SyncChange).where(
                SyncChange.entity_sync_id == "tx-X",
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].action == "delete"


def test_compact_isolates_other_entities():
    """只清目标 entity 的 upsert,不影响其它 entity 或其它 user 的 events。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_shared_ledger(db)
        # 目标:tx-X owner
        db.add(_make_sync_change(
            user_id="owner", entity_type="transaction", entity_sync_id="tx-X",
            action="upsert", ledger_id="L1",
        ))
        db.add(_make_sync_change(
            user_id="owner", entity_type="transaction", entity_sync_id="tx-X",
            action="delete", ledger_id="L1",
        ))
        # 另一 tx,不该动
        db.add(_make_sync_change(
            user_id="owner", entity_type="transaction", entity_sync_id="tx-Y",
            action="upsert", ledger_id="L1",
        ))
        # 同 sync_id 但不同 entity_type,不该动
        db.add(_make_sync_change(
            user_id="owner", entity_type="budget", entity_sync_id="tx-X",
            action="upsert", ledger_id="L1",
        ))
        # 另一 user 但同 sync_id,不该动
        db.add(_make_sync_change(
            user_id="editor", entity_type="transaction", entity_sync_id="tx-X",
            action="upsert", ledger_id="L1",
        ))
        db.commit()

        n = _compact_entity_upsert_events(
            db, user_id="owner", entity_type="transaction",
            entity_sync_id="tx-X",
        )
        db.commit()

        assert n == 1
        # tx-X owner transaction 只剩 delete(注意:同 sync_id 不同 entity_type
        # 的 budget tx-X 不在这一查询里,也不该受影响)
        owner_tx_x = db.scalars(
            select(SyncChange).where(
                SyncChange.user_id == "owner",
                SyncChange.entity_type == "transaction",
                SyncChange.entity_sync_id == "tx-X",
            )
        ).all()
        assert len(owner_tx_x) == 1
        assert owner_tx_x[0].action == "delete"

        # 其它都保留
        assert db.scalar(
            select(SyncChange).where(SyncChange.entity_sync_id == "tx-Y")
        ) is not None
        assert db.scalar(
            select(SyncChange).where(
                SyncChange.entity_type == "budget",
                SyncChange.entity_sync_id == "tx-X",
            )
        ) is not None
        assert db.scalar(
            select(SyncChange).where(
                SyncChange.user_id == "editor",
                SyncChange.entity_sync_id == "tx-X",
            )
        ) is not None


def test_compact_no_events_returns_zero():
    """没有匹配 row → 返 0,不报错。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_shared_ledger(db)
        n = _compact_entity_upsert_events(
            db, user_id="owner", entity_type="transaction",
            entity_sync_id="never-existed",
        )
        db.commit()
        assert n == 0


# ============================================================================
# 集成:_delete_tx 完整路径
# ============================================================================


def test_delete_tx_cleans_editor_attachment_and_compacts_log(tmp_path):
    """完整路径:Editor 上传附件 + tx → delete 来自 owner-scope SyncChange:
    1. tx projection 行被删
    2. Editor 上传的 attachment_files 行被清(ledger-scope GC)
    3. 该 tx 的 upsert events 被压缩,只剩 delete event
    """
    session_factory = _make_db()
    with session_factory() as db:
        _seed_shared_ledger(db)
        att, path = _make_attachment(
            tmp_path, "f-by-editor", ledger_id="L1", user_id="editor",
        )
        db.add(att)
        # tx projection 行(共享账本里挂 owner 的 user_id)
        upsert_tx(
            db,
            ledger_id="L1",
            user_id="owner",
            source_change_id=10,
            payload={
                "syncId": "tx-DEL",
                "type": "expense",
                "amount": 10.0,
                "happenedAt": "2026-05-28T00:00:00Z",
                "attachments": [
                    {"fileName": "a.png", "cloudFileId": "f-by-editor"},
                ],
            },
        )
        # 历史 upsert events(owner-scope log)
        db.add(_make_sync_change(
            user_id="owner", entity_type="transaction", entity_sync_id="tx-DEL",
            action="upsert", ledger_id="L1",
        ))
        db.add(_make_sync_change(
            user_id="owner", entity_type="transaction", entity_sync_id="tx-DEL",
            action="upsert", ledger_id="L1",
        ))
        # delete event
        db.add(_make_sync_change(
            user_id="owner", entity_type="transaction", entity_sync_id="tx-DEL",
            action="delete", ledger_id="L1",
        ))
        db.commit()

        # 模拟 sync_applier 调 _delete_tx 时传 owner 的 user_id(ledger_owner_id)
        _delete_tx(db, ledger_id="L1", sync_id="tx-DEL", user_id="owner")
        db.commit()

        # 1. tx projection 行已删
        assert db.scalar(
            select(ReadTxProjection).where(ReadTxProjection.sync_id == "tx-DEL")
        ) is None

        # 2. Editor 上传的 attachment 行被清
        assert db.get(AttachmentFile, "f-by-editor") is None
        assert not path.exists()

        # 3. sync_changes 里 tx-DEL 只剩 delete event
        remaining = db.scalars(
            select(SyncChange).where(SyncChange.entity_sync_id == "tx-DEL")
        ).all()
        assert len(remaining) == 1
        assert remaining[0].action == "delete"


# ============================================================================
# 其它 entity 类型的 compaction
# ============================================================================


def test_delete_user_account_compacts():
    """删 account 也清掉 upsert 历史。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_shared_ledger(db)
        db.add(
            UserAccountProjection(
                user_id="owner", sync_id="acc-X", name="cash",
                account_type="cash", currency="CNY", initial_balance=0.0,
                source_change_id=1,
            )
        )
        db.add(_make_sync_change(
            user_id="owner", entity_type="account", entity_sync_id="acc-X",
            action="upsert", scope="user",
        ))
        db.add(_make_sync_change(
            user_id="owner", entity_type="account", entity_sync_id="acc-X",
            action="delete", scope="user",
        ))
        db.commit()

        _delete_user_account(db, user_id="owner", sync_id="acc-X")
        db.commit()

        remaining = db.scalars(
            select(SyncChange).where(SyncChange.entity_sync_id == "acc-X")
        ).all()
        assert len(remaining) == 1
        assert remaining[0].action == "delete"


def test_delete_user_tag_compacts():
    """删 tag 也清掉 upsert 历史。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_shared_ledger(db)
        db.add(
            UserTagProjection(
                user_id="owner", sync_id="tag-X", name="dinner",
                source_change_id=1,
            )
        )
        db.add(_make_sync_change(
            user_id="owner", entity_type="tag", entity_sync_id="tag-X",
            action="upsert", scope="user",
        ))
        db.commit()

        _delete_user_tag(db, user_id="owner", sync_id="tag-X")
        db.commit()

        # 没有 delete event 也照样压缩(虽然语义上有点怪,但实际不会发生 —
        # 调用方一定是 delete event handler,此时 delete event 是 caller 自己
        # 拿着的那一条,还没写进 sync_changes 表,要不要写视上下游 router 决定)
        # 这里测的是 _compact 函数本身的行为:删掉所有 upsert
        remaining = db.scalars(
            select(SyncChange).where(SyncChange.entity_sync_id == "tag-X")
        ).all()
        assert len(remaining) == 0


def test_delete_user_category_compacts():
    """删 category 同样压缩(category 还有 icon GC 路径要走,确认它没破)。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_shared_ledger(db)
        db.add(
            UserCategoryProjection(
                user_id="owner", sync_id="cat-X", name="food",
                kind="expense", source_change_id=1,
            )
        )
        db.add(_make_sync_change(
            user_id="owner", entity_type="category", entity_sync_id="cat-X",
            action="upsert", scope="user",
        ))
        db.add(_make_sync_change(
            user_id="owner", entity_type="category", entity_sync_id="cat-X",
            action="delete", scope="user",
        ))
        db.commit()

        _delete_user_category(db, user_id="owner", sync_id="cat-X")
        db.commit()

        remaining = db.scalars(
            select(SyncChange).where(SyncChange.entity_sync_id == "cat-X")
        ).all()
        assert len(remaining) == 1
        assert remaining[0].action == "delete"


def test_delete_budget_compacts():
    """删 budget 也清掉 upsert 历史。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_shared_ledger(db)
        db.add(_make_sync_change(
            user_id="owner", entity_type="budget", entity_sync_id="bud-X",
            action="upsert", ledger_id="L1", scope="ledger",
        ))
        db.commit()

        _delete_budget(db, ledger_id="L1", sync_id="bud-X", user_id="owner")
        db.commit()

        remaining = db.scalars(
            select(SyncChange).where(SyncChange.entity_sync_id == "bud-X")
        ).all()
        assert len(remaining) == 0
