"""data_cleanup 扫描 / 清理服务单测。

每类孤儿场景构造 → scan_all 命中 → clean → 重扫为空 + 关键 invariant 验证。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base
from src.models import (
    AttachmentFile,
    Ledger,
    ReadBudgetProjection,
    ReadTxProjection,
    SyncChange,
    User,
    UserAccountProjection,
    UserCategoryProjection,
)
from src.services.data_cleanup import (
    OrphanType,
    clean,
    scan_all,
)


# ─────────────────────── fixtures ───────────────────────


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed_user_ledger(db, user_id="U1", ledger_id="L1"):
    db.add(User(id=user_id, email=f"{user_id}@x.com", password_hash="h"))
    db.add(
        Ledger(
            id=ledger_id,
            user_id=user_id,
            external_id=f"ext-{ledger_id}",
            name="L",
            currency="CNY",
        )
    )
    db.flush()


def _add_tx(
    db,
    *,
    ledger_id="L1",
    user_id="U1",
    sync_id="tx-1",
    category_sync_id=None,
    account_sync_id=None,
    from_account_sync_id=None,
    to_account_sync_id=None,
    attachments_json=None,
):
    db.add(
        ReadTxProjection(
            ledger_id=ledger_id,
            sync_id=sync_id,
            user_id=user_id,
            tx_type="expense",
            amount=10.0,
            happened_at=datetime.now(timezone.utc),
            category_sync_id=category_sync_id,
            account_sync_id=account_sync_id,
            from_account_sync_id=from_account_sync_id,
            to_account_sync_id=to_account_sync_id,
            attachments_json=attachments_json,
        )
    )
    db.flush()


def _add_category(db, sync_id, *, user_id="U1"):
    db.add(
        UserCategoryProjection(
            user_id=user_id, sync_id=sync_id, name="c", kind="expense"
        )
    )
    db.flush()


def _add_account(db, sync_id, *, user_id="U1"):
    db.add(UserAccountProjection(user_id=user_id, sync_id=sync_id, name="acc"))
    db.flush()


# ─────────────────────── A 类 ───────────────────────


def test_A1_tx_missing_category(session_factory):
    with session_factory() as db:
        _seed_user_ledger(db)
        _add_tx(db, sync_id="tx-1", category_sync_id="ghost-cat")
        # 不加 category projection 行 → ghost-cat 不存在 → 孤儿
        db.commit()

        report = scan_all(db)
        assert any(
            r.type == OrphanType.TX_MISSING_CATEGORY for r in report.db_orphans
        ), "应扫到 tx 失主 category"


def test_A2_tx_missing_account(session_factory):
    with session_factory() as db:
        _seed_user_ledger(db)
        _add_tx(db, sync_id="tx-1", account_sync_id="ghost-acc")
        db.commit()
        report = scan_all(db)
        assert any(r.type == OrphanType.TX_MISSING_ACCOUNT for r in report.db_orphans)


def test_A3_transfer_missing_to_account(session_factory):
    with session_factory() as db:
        _seed_user_ledger(db)
        _add_account(db, "valid-from")
        # to_account_sync_id 指向已删账户
        _add_tx(
            db,
            sync_id="tx-1",
            from_account_sync_id="valid-from",
            to_account_sync_id="ghost-to",
        )
        db.commit()
        report = scan_all(db)
        types = {r.type for r in report.db_orphans}
        assert OrphanType.TX_MISSING_TO_ACCOUNT in types
        # from 有效不应命中
        assert OrphanType.TX_MISSING_FROM_ACCOUNT not in types


def test_A4_budget_missing_category(session_factory):
    with session_factory() as db:
        _seed_user_ledger(db)
        db.add(
            ReadBudgetProjection(
                ledger_id="L1",
                sync_id="b-1",
                user_id="U1",
                budget_type="category",
                category_sync_id="ghost-cat",
                amount=100,
            )
        )
        db.commit()
        report = scan_all(db)
        assert any(r.type == OrphanType.BUDGET_MISSING_CATEGORY for r in report.db_orphans)


# ─────────────────────── B 类(文件)─────────────────────


def test_B1_attachment_no_ref(tmp_path: Path, session_factory):
    with session_factory() as db:
        _seed_user_ledger(db)
        sp = tmp_path / "orphan.png"
        sp.write_bytes(b"data")
        db.add(
            AttachmentFile(
                id="att-orphan",
                ledger_id="L1",
                user_id="U1",
                sha256="aaa",
                size_bytes=4,
                file_name="orphan.png",
                storage_path=str(sp),
                attachment_kind="transaction",
            )
        )
        db.commit()

        report = scan_all(db)
        assert any(r.type == OrphanType.ATTACHMENT_NO_REF for r in report.file_orphans)


def test_B2_attachment_file_missing(tmp_path: Path, session_factory):
    with session_factory() as db:
        _seed_user_ledger(db)
        # storage_path 指向不存在的路径
        ghost_path = str(tmp_path / "does-not-exist.png")
        db.add(
            AttachmentFile(
                id="att-missing",
                ledger_id="L1",
                user_id="U1",
                sha256="bbb",
                size_bytes=10,
                file_name="x.png",
                storage_path=ghost_path,
                attachment_kind="transaction",
            )
        )
        db.commit()

        report = scan_all(db)
        types = {r.type for r in report.file_orphans}
        assert OrphanType.ATTACHMENT_FILE_MISSING in types


def test_B3_disk_file_no_row(tmp_path: Path, session_factory):
    with session_factory() as db:
        _seed_user_ledger(db)
        root = tmp_path / "atts"
        root.mkdir()
        # 磁盘有文件但 DB 无对应 storage_path
        (root / "u1").mkdir()
        (root / "u1" / "stray.png").write_bytes(b"x")
        db.commit()

        report = scan_all(db, attachments_root=root)
        assert any(r.type == OrphanType.DISK_FILE_NO_ROW for r in report.file_orphans)


def test_B4_tx_ref_broken_attachment(session_factory):
    with session_factory() as db:
        _seed_user_ledger(db)
        # attachments_json 引用 fileId,但 attachment_files 表没这个 id
        atts = [{"fileName": "a.jpg", "cloudFileId": "ghost-file"}]
        _add_tx(db, sync_id="tx-1", attachments_json=json.dumps(atts))
        db.commit()

        report = scan_all(db)
        assert any(
            r.type == OrphanType.TX_REF_BROKEN_ATTACHMENT for r in report.file_orphans
        )


# ─────────────────────── C 类(sync_changes)─────────────────────


def test_A5_sync_change_missing_entity(session_factory):
    with session_factory() as db:
        _seed_user_ledger(db)
        db.add(
            SyncChange(
                user_id="U1",
                ledger_id="L1",
                entity_type="transaction",
                entity_sync_id="ghost-tx",
                action="update",
                payload_json={},
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        report = scan_all(db)
        assert any(
            r.type == OrphanType.SYNC_CHANGE_MISSING_ENTITY for r in report.sync_orphans
        )


def test_sync_change_delete_action_not_orphan(session_factory):
    """action=delete 的 change 即使实体不存在也不算孤儿。"""
    with session_factory() as db:
        _seed_user_ledger(db)
        db.add(
            SyncChange(
                user_id="U1",
                ledger_id="L1",
                entity_type="transaction",
                entity_sync_id="ghost-tx",
                action="delete",
                payload_json={},
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        report = scan_all(db)
        assert not any(
            r.type == OrphanType.SYNC_CHANGE_MISSING_ENTITY for r in report.sync_orphans
        )


# ─────────────────────── cleaner ───────────────────────


def test_clean_A1_clears_category_sync_id_but_keeps_tx(session_factory):
    """A1 cleaner 把 category_sync_id 置 null,tx 行本身保留。"""
    with session_factory() as db:
        _seed_user_ledger(db)
        _add_tx(db, sync_id="tx-1", category_sync_id="ghost-cat")
        db.commit()

        report = scan_all(db)
        records = [r for r in report.db_orphans if r.type == OrphanType.TX_MISSING_CATEGORY]
        result = clean(db, records)
        db.commit()
        assert result.success_count == len(records)

        # tx 仍在,category_sync_id 已 null
        tx = db.get(ReadTxProjection, ("L1", "tx-1"))
        assert tx is not None
        assert tx.category_sync_id is None

        # 重扫无该类孤儿
        report2 = scan_all(db)
        assert not any(
            r.type == OrphanType.TX_MISSING_CATEGORY for r in report2.db_orphans
        )


def test_clean_B1_deletes_row_and_file(tmp_path: Path, session_factory):
    """B1 cleaner 同时删 DB 行 + 物理文件。"""
    with session_factory() as db:
        _seed_user_ledger(db)
        sp = tmp_path / "orphan.png"
        sp.write_bytes(b"d")
        db.add(
            AttachmentFile(
                id="att-orphan",
                ledger_id="L1",
                user_id="U1",
                sha256="zz",
                size_bytes=1,
                file_name="orphan.png",
                storage_path=str(sp),
                attachment_kind="transaction",
            )
        )
        db.commit()

        report = scan_all(db)
        records = [r for r in report.file_orphans if r.type == OrphanType.ATTACHMENT_NO_REF]
        assert records
        result = clean(db, records)
        db.commit()
        assert result.success_count == len(records)
        assert db.get(AttachmentFile, "att-orphan") is None
        assert not sp.exists()


def test_clean_B4_strips_broken_attachments(session_factory):
    """B4 cleaner 把 attachments_json 里 broken 项移除,保留 tx 本体。"""
    with session_factory() as db:
        _seed_user_ledger(db)
        atts = [
            {"fileName": "ok.jpg", "cloudFileId": "valid-file"},
            {"fileName": "bad.jpg", "cloudFileId": "ghost-file"},
        ]
        _add_tx(db, sync_id="tx-1", attachments_json=json.dumps(atts))
        # 创建 valid-file 对应的 attachment_files 行
        db.add(
            AttachmentFile(
                id="valid-file",
                ledger_id="L1",
                user_id="U1",
                sha256="vv",
                size_bytes=1,
                file_name="ok.jpg",
                storage_path="/tmp/ok",
                attachment_kind="transaction",
            )
        )
        db.commit()

        report = scan_all(db)
        records = [r for r in report.file_orphans if r.type == OrphanType.TX_REF_BROKEN_ATTACHMENT]
        assert records
        result = clean(db, records)
        db.commit()
        assert result.success_count == len(records)

        tx = db.get(ReadTxProjection, ("L1", "tx-1"))
        assert tx is not None
        kept = json.loads(tx.attachments_json)
        assert len(kept) == 1
        assert kept[0]["cloudFileId"] == "valid-file"


def test_clean_B1_removes_empty_parent_dirs(tmp_path: Path, session_factory, monkeypatch):
    """B1 删完文件后,空的父目录也清掉(直到遇到非空目录或 root)。

    模拟 attachment_storage_dir = tmp_path/root,文件路径
    root/user/ledger/sha2/file.png → 删后 sha2 / ledger / user 都清掉,
    但 root 本身保留。
    """
    root = tmp_path / "atts"
    root.mkdir()
    # 让 _storage_root() 返回我们的 tmp root
    from src import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "attachment_storage_dir", str(root))

    with session_factory() as db:
        _seed_user_ledger(db)
        # 构造嵌套目录:root/u/l/ab/file.png
        nested = root / "u" / "l" / "ab"
        nested.mkdir(parents=True)
        file_path = nested / "file.png"
        file_path.write_bytes(b"d")
        db.add(
            AttachmentFile(
                id="att-1",
                ledger_id="L1",
                user_id="U1",
                sha256="abc",
                size_bytes=1,
                file_name="file.png",
                storage_path=str(file_path),
                attachment_kind="transaction",
            )
        )
        db.commit()

        report = scan_all(db)
        records = [r for r in report.file_orphans if r.type == OrphanType.ATTACHMENT_NO_REF]
        assert records
        result = clean(db, records)
        db.commit()
        assert result.success_count == len(records)
        assert not file_path.exists()
        # 三级父目录都该被清空 rmdir
        assert not nested.exists()
        assert not (root / "u" / "l").exists()
        assert not (root / "u").exists()
        # root 本身保留
        assert root.exists()


def test_clean_B1_keeps_non_empty_parent(tmp_path: Path, session_factory, monkeypatch):
    """sibling 文件还在时,父目录不删。"""
    root = tmp_path / "atts"
    root.mkdir()
    from src import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "attachment_storage_dir", str(root))

    with session_factory() as db:
        _seed_user_ledger(db)
        nested = root / "u" / "l" / "ab"
        nested.mkdir(parents=True)
        orphan = nested / "orphan.png"
        sibling = nested / "sibling.png"
        orphan.write_bytes(b"o")
        sibling.write_bytes(b"s")  # 留一个邻居,目录不能删
        db.add(
            AttachmentFile(
                id="att-orphan",
                ledger_id="L1",
                user_id="U1",
                sha256="abc",
                size_bytes=1,
                file_name="orphan.png",
                storage_path=str(orphan),
                attachment_kind="transaction",
            )
        )
        db.commit()

        report = scan_all(db)
        records = [r for r in report.file_orphans if r.type == OrphanType.ATTACHMENT_NO_REF]
        result = clean(db, records)
        db.commit()
        assert result.success_count == len(records)
        assert not orphan.exists()
        assert sibling.exists(), "sibling 不应被误删"
        assert nested.exists(), "父目录非空时不能 rmdir"


def test_empty_records_no_op(session_factory):
    with session_factory() as db:
        _seed_user_ledger(db)
        db.commit()
        result = clean(db, [])
        assert result.success_count == 0
        assert result.failures == []
