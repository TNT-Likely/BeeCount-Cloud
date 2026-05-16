"""Attachment GC 单测 —— 锁定 gc_orphan_attachments 的核心行为。

删交易 / 删分类时调 gc_orphan_attachments(ledger_id, file_ids)。期望:
  - 无引用的 AttachmentFile → DELETE 行 + unlink 物理文件
  - 还有 tx 引用(attachments_json 含同 fileId)→ 保留
  - 还有 category 引用(icon_cloud_file_id=fileId)→ 保留
  - AttachmentFile 本身不存在 → 静默跳过(不抛)
  - 物理文件 unlink 失败 → 只 warn,DB 行已删
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base
from src.models import (
    AttachmentFile,
    Ledger,
    ReadCategoryProjection,
    ReadTxProjection,
    User,
)
from src.projection import gc_orphan_attachments, upsert_tx


def _make_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed_ledger(db, ledger_id="L1", user_id="U1"):
    db.add(User(id=user_id, email="u@example.com", password_hash="h"))
    ledger = Ledger(id=ledger_id, user_id=user_id, external_id="ext", name="L", currency="CNY")
    db.add(ledger)
    db.flush()
    # 共享账本 Phase 1:创建账本同时必须建 owner member 行,否则后续 access 失败。
    from src.ledger_access import ensure_owner_member
    ensure_owner_member(db, ledger=ledger)


def _make_attachment(tmp_path: Path, file_id: str, ledger_id: str = "L1", user_id: str = "U1"):
    """创建 AttachmentFile + 物理文件,返回 (row, storage_path)。"""
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


def test_gc_removes_orphan_file(tmp_path):
    """无引用的 AttachmentFile → 行删 + 磁盘 unlink。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_ledger(db)
        att, path = _make_attachment(tmp_path, "f-orphan")
        db.add(att)
        db.commit()

        n = gc_orphan_attachments(db, ledger_id="L1", file_ids={"f-orphan"})
        db.commit()

        assert n == 1
        assert not path.exists(), "物理文件应已 unlink"
        assert db.get(AttachmentFile, "f-orphan") is None


def test_gc_preserves_tx_referenced_file(tmp_path):
    """tx 还引用该 fileId → 不删。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_ledger(db)
        att, path = _make_attachment(tmp_path, "f-shared")
        db.add(att)
        # tx projection 引用这个 fileId
        db.add(
            ReadTxProjection(
                ledger_id="L1",
                sync_id="tx-1",
                user_id="U1",
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

        n = gc_orphan_attachments(db, ledger_id="L1", file_ids={"f-shared"})
        db.commit()

        assert n == 0
        assert path.exists()
        assert db.get(AttachmentFile, "f-shared") is not None


def test_gc_preserves_category_icon_referenced_file(tmp_path):
    """category.icon_cloud_file_id 还指向 → 不删。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_ledger(db)
        att, path = _make_attachment(tmp_path, "f-iconshared")
        db.add(att)
        db.add(
            ReadCategoryProjection(
                ledger_id="L1",
                sync_id="cat-other",  # 不是我们在删的那个分类
                user_id="U1",
                name="other-cat",
                kind="expense",
                icon="custom-icon",
                icon_type="custom",
                icon_cloud_file_id="f-iconshared",
                source_change_id=1,
            )
        )
        db.commit()

        n = gc_orphan_attachments(db, ledger_id="L1", file_ids={"f-iconshared"})
        db.commit()

        assert n == 0
        assert path.exists()


def test_gc_skips_missing_attachment_file(tmp_path):
    """AttachmentFile 表里根本没这个 id → 静默跳过,不抛,计数 0。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_ledger(db)
        # 不插 AttachmentFile
        n = gc_orphan_attachments(db, ledger_id="L1", file_ids={"f-nonexistent"})
        db.commit()
        assert n == 0


def test_gc_unlink_failure_still_deletes_row(tmp_path, caplog):
    """storage_path 指向不存在的文件 → DB 行仍删,物理 unlink 静默跳过。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_ledger(db)
        att, path = _make_attachment(tmp_path, "f-nofile")
        db.add(att)
        db.commit()

        # 人为删文件先,模拟磁盘已清
        os.unlink(path)
        assert not path.exists()

        n = gc_orphan_attachments(db, ledger_id="L1", file_ids={"f-nofile"})
        db.commit()
        assert n == 1
        assert db.get(AttachmentFile, "f-nofile") is None


def test_upsert_tx_gc_removes_detached_attachment(tmp_path):
    """交易原本有 2 个附件,app 删掉其中 1 个后 upsert_tx 进来只带 1 个 →
    被剔除的那个 fileId 应该触发 GC(AttachmentFile 行删 + 物理文件 unlink),
    保留的那个不动。覆盖 2026-04 发现的 bug:老代码只写新 attachments_json,
    不 diff 旧值,导致 web /data/attachments 残留已被删的附件。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_ledger(db)
        att_keep, path_keep = _make_attachment(tmp_path, "f-keep")
        att_drop, path_drop = _make_attachment(tmp_path, "f-drop")
        db.add_all([att_keep, att_drop])
        db.commit()

        # 初始:tx 带两个附件
        upsert_tx(
            db,
            ledger_id="L1",
            user_id="U1",
            source_change_id=1,
            payload={
                "syncId": "tx-multi",
                "type": "expense",
                "amount": 10.0,
                "happenedAt": "2026-04-21T00:00:00Z",
                "attachments": [
                    {"fileName": "a.png", "cloudFileId": "f-keep"},
                    {"fileName": "b.png", "cloudFileId": "f-drop"},
                ],
            },
        )
        db.commit()
        assert path_keep.exists() and path_drop.exists()
        assert db.get(AttachmentFile, "f-keep") is not None
        assert db.get(AttachmentFile, "f-drop") is not None

        # App 删掉 f-drop 后的再次推送:只带 f-keep
        upsert_tx(
            db,
            ledger_id="L1",
            user_id="U1",
            source_change_id=2,
            payload={
                "syncId": "tx-multi",
                "type": "expense",
                "amount": 10.0,
                "happenedAt": "2026-04-21T00:00:00Z",
                "attachments": [
                    {"fileName": "a.png", "cloudFileId": "f-keep"},
                ],
            },
        )
        db.commit()

        # f-drop 应已 GC
        assert db.get(AttachmentFile, "f-drop") is None
        assert not path_drop.exists(), "被剔除的附件物理文件应已 unlink"
        # f-keep 应保留
        assert db.get(AttachmentFile, "f-keep") is not None
        assert path_keep.exists()


def test_upsert_tx_gc_clears_all_when_attachments_removed(tmp_path):
    """交易原本有附件,app 把全部附件都删了 → attachments_json 变 null,
    原有的 AttachmentFile 应全部 GC 掉(确认 prev_file_ids 抓取路径对空 new
    的差集正确)。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_ledger(db)
        att, path = _make_attachment(tmp_path, "f-only")
        db.add(att)
        db.commit()

        upsert_tx(
            db,
            ledger_id="L1",
            user_id="U1",
            source_change_id=1,
            payload={
                "syncId": "tx-empty-after",
                "type": "expense",
                "amount": 5.0,
                "happenedAt": "2026-04-21T00:00:00Z",
                "attachments": [
                    {"fileName": "x.png", "cloudFileId": "f-only"},
                ],
            },
        )
        db.commit()
        assert db.get(AttachmentFile, "f-only") is not None

        # 清空附件
        upsert_tx(
            db,
            ledger_id="L1",
            user_id="U1",
            source_change_id=2,
            payload={
                "syncId": "tx-empty-after",
                "type": "expense",
                "amount": 5.0,
                "happenedAt": "2026-04-21T00:00:00Z",
                "attachments": [],
            },
        )
        db.commit()

        assert db.get(AttachmentFile, "f-only") is None
        assert not path.exists()


def test_upsert_tx_preserves_file_shared_by_another_tx(tmp_path):
    """同一 fileId 被两笔 tx 共享(mobile 端同一附件贴到多条交易) → 从其中
    一笔移除时仍有另一笔引用,AttachmentFile 不应被 GC。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_ledger(db)
        att, path = _make_attachment(tmp_path, "f-dual")
        db.add(att)
        db.commit()

        # tx-A 和 tx-B 都带这个附件
        for sid in ("tx-A", "tx-B"):
            upsert_tx(
                db,
                ledger_id="L1",
                user_id="U1",
                source_change_id=1,
                payload={
                    "syncId": sid,
                    "type": "expense",
                    "amount": 1.0,
                    "happenedAt": "2026-04-21T00:00:00Z",
                    "attachments": [
                        {"fileName": "shared.png", "cloudFileId": "f-dual"},
                    ],
                },
            )
        db.commit()

        # tx-A 把附件删了
        upsert_tx(
            db,
            ledger_id="L1",
            user_id="U1",
            source_change_id=2,
            payload={
                "syncId": "tx-A",
                "type": "expense",
                "amount": 1.0,
                "happenedAt": "2026-04-21T00:00:00Z",
                "attachments": [],
            },
        )
        db.commit()

        # tx-B 还在引用 → 保留
        assert db.get(AttachmentFile, "f-dual") is not None
        assert path.exists()


def test_gc_dedup_and_empty_input(tmp_path):
    """空 set / None / 重复 id 正确处理。"""
    session_factory = _make_db()
    with session_factory() as db:
        _seed_ledger(db)
        att, path = _make_attachment(tmp_path, "f-dup")
        db.add(att)
        db.commit()

        # 重复 + None
        n = gc_orphan_attachments(
            db, ledger_id="L1", file_ids=["f-dup", None, "f-dup", "", "  "]
        )
        db.commit()
        assert n == 1  # 只清 1 次,不重复

        # 空输入 → 0
        assert gc_orphan_attachments(db, ledger_id="L1", file_ids=set()) == 0
        assert gc_orphan_attachments(db, ledger_id="L1", file_ids=[]) == 0
