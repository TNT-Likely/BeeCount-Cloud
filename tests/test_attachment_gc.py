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
from src.projection import gc_orphan_attachments


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
    db.add(
        Ledger(id=ledger_id, user_id=user_id, external_id="ext", name="L", currency="CNY")
    )
    db.flush()


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
