"""孤儿数据扫描 — admin scope,跨所有用户。

每个 `_scan_*` 私有函数对应 plan 检测清单的一项;`scan_all` 聚合返
[ScanReport][src.services.data_cleanup.models.ScanReport]。SQL 用 SA Core
表达式,EXISTS / NOT IN / LEFT JOIN 几种模式都涉及。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from sqlalchemy import literal_column, or_, select
from sqlalchemy.orm import Session, aliased

from ...models import (
    AttachmentFile,
    ReadBudgetProjection,
    ReadTxProjection,
    SyncChange,
    UserAccountProjection,
    UserCategoryProjection,
    UserTagProjection,
)
from .models import OrphanRecord, OrphanType, ScanReport


import logging

logger = logging.getLogger("beecount.data_cleanup")


def scan_all(db: Session, *, attachments_root: Path | None = None) -> ScanReport:
    """跑全部扫描。`attachments_root` 给 B3 用,不传则跳过磁盘文件扫描。"""
    db_orphans = [
        *_scan_tx_missing_category(db),
        *_scan_tx_missing_account(db),
        *_scan_tx_missing_from_account(db),
        *_scan_tx_missing_to_account(db),
        *_scan_budget_missing_category(db),
    ]
    file_orphans = [
        *_scan_attachment_no_ref(db),
        *_scan_attachment_file_missing(db),
        *_scan_tx_ref_broken_attachment(db),
    ]
    if attachments_root is not None:
        file_orphans.extend(_scan_disk_file_no_row(db, attachments_root))
    sync_orphans = [*_scan_sync_change_missing_entity(db)]
    return ScanReport(
        db_orphans=db_orphans,
        file_orphans=file_orphans,
        sync_orphans=sync_orphans,
    )


# ─────────────────────── A 类:DB 引用断链 ───────────────────────


def _scan_tx_missing_category(db: Session) -> list[OrphanRecord]:
    """A1 — read_tx_projection.category_sync_id 在 user_category_projection 不存在。

    user-global 维度查 — 同 user_id 范围内 category sync_id 集合反查。
    """
    cat = UserCategoryProjection
    tx = ReadTxProjection
    stmt = (
        select(tx.user_id, tx.ledger_id, tx.sync_id, tx.amount, tx.tx_type, tx.category_sync_id)
        .where(tx.category_sync_id.isnot(None))
        .where(
            ~select(literal_column("1"))
            .where(
                cat.user_id == tx.user_id,
                cat.sync_id == tx.category_sync_id,
            )
            .select_from(cat)
            .exists()
        )
    )
    return [
        OrphanRecord(
            type=OrphanType.TX_MISSING_CATEGORY,
            user_id=row.user_id,
            row_id=f"{row.ledger_id}:{row.sync_id}",
            sync_id=row.sync_id,
            title=f"交易 {row.sync_id[:8]} (¥{row.amount:.2f})",
            subtitle=f"分类已删 categorySyncId={row.category_sync_id[:8]}…",
            extra={"ledger_id": row.ledger_id, "sync_id": row.sync_id},
        )
        for row in db.execute(stmt).all()
    ]


def _scan_tx_missing_account(db: Session) -> list[OrphanRecord]:
    """A2 — read_tx_projection.account_sync_id 在 user_account_projection 不存在。"""
    acc = UserAccountProjection
    tx = ReadTxProjection
    stmt = (
        select(tx.user_id, tx.ledger_id, tx.sync_id, tx.amount, tx.account_sync_id)
        .where(tx.account_sync_id.isnot(None))
        .where(
            ~select(literal_column("1"))
            .where(acc.user_id == tx.user_id, acc.sync_id == tx.account_sync_id)
            .select_from(acc)
            .exists()
        )
    )
    return [
        OrphanRecord(
            type=OrphanType.TX_MISSING_ACCOUNT,
            user_id=row.user_id,
            row_id=f"{row.ledger_id}:{row.sync_id}",
            sync_id=row.sync_id,
            title=f"交易 {row.sync_id[:8]} (¥{row.amount:.2f})",
            subtitle=f"账户已删 accountSyncId={row.account_sync_id[:8]}…",
            extra={"ledger_id": row.ledger_id, "sync_id": row.sync_id, "field": "account_sync_id"},
        )
        for row in db.execute(stmt).all()
    ]


def _scan_tx_missing_from_account(db: Session) -> list[OrphanRecord]:
    """A3a — 转账 tx.from_account_sync_id 已删。"""
    acc = UserAccountProjection
    tx = ReadTxProjection
    stmt = (
        select(tx.user_id, tx.ledger_id, tx.sync_id, tx.amount, tx.from_account_sync_id)
        .where(tx.from_account_sync_id.isnot(None))
        .where(
            ~select(literal_column("1"))
            .where(acc.user_id == tx.user_id, acc.sync_id == tx.from_account_sync_id)
            .select_from(acc)
            .exists()
        )
    )
    return [
        OrphanRecord(
            type=OrphanType.TX_MISSING_FROM_ACCOUNT,
            user_id=row.user_id,
            row_id=f"{row.ledger_id}:{row.sync_id}",
            sync_id=row.sync_id,
            title=f"转账 {row.sync_id[:8]} (¥{row.amount:.2f})",
            subtitle=f"转出账户已删 fromAccountSyncId={row.from_account_sync_id[:8]}…",
            extra={"ledger_id": row.ledger_id, "sync_id": row.sync_id, "field": "from_account_sync_id"},
        )
        for row in db.execute(stmt).all()
    ]


def _scan_tx_missing_to_account(db: Session) -> list[OrphanRecord]:
    """A3b — 转账 tx.to_account_sync_id 已删。"""
    acc = UserAccountProjection
    tx = ReadTxProjection
    stmt = (
        select(tx.user_id, tx.ledger_id, tx.sync_id, tx.amount, tx.to_account_sync_id)
        .where(tx.to_account_sync_id.isnot(None))
        .where(
            ~select(literal_column("1"))
            .where(acc.user_id == tx.user_id, acc.sync_id == tx.to_account_sync_id)
            .select_from(acc)
            .exists()
        )
    )
    return [
        OrphanRecord(
            type=OrphanType.TX_MISSING_TO_ACCOUNT,
            user_id=row.user_id,
            row_id=f"{row.ledger_id}:{row.sync_id}",
            sync_id=row.sync_id,
            title=f"转账 {row.sync_id[:8]} (¥{row.amount:.2f})",
            subtitle=f"转入账户已删 toAccountSyncId={row.to_account_sync_id[:8]}…",
            extra={"ledger_id": row.ledger_id, "sync_id": row.sync_id, "field": "to_account_sync_id"},
        )
        for row in db.execute(stmt).all()
    ]


def _scan_budget_missing_category(db: Session) -> list[OrphanRecord]:
    """A4 — read_budget_projection.category_sync_id 已删。"""
    cat = UserCategoryProjection
    b = ReadBudgetProjection
    stmt = (
        select(b.user_id, b.ledger_id, b.sync_id, b.amount, b.budget_type, b.category_sync_id)
        .where(b.category_sync_id.isnot(None))
        .where(
            ~select(literal_column("1"))
            .where(cat.user_id == b.user_id, cat.sync_id == b.category_sync_id)
            .select_from(cat)
            .exists()
        )
    )
    return [
        OrphanRecord(
            type=OrphanType.BUDGET_MISSING_CATEGORY,
            user_id=row.user_id,
            row_id=f"{row.ledger_id}:{row.sync_id}",
            sync_id=row.sync_id,
            title=f"预算 {row.sync_id[:8]} (¥{(row.amount or 0):.0f})",
            subtitle=f"分类已删 categorySyncId={row.category_sync_id[:8]}…",
            extra={"ledger_id": row.ledger_id, "sync_id": row.sync_id},
        )
        for row in db.execute(stmt).all()
    ]


# ─────────────────────── C 类:sync_changes ───────────────────────


def _scan_sync_change_missing_entity(db: Session) -> list[OrphanRecord]:
    """A5/C1 — sync_changes 引用的实体已不存在(非 delete action)。

    entity_type → 对应表的 sync_id 列。delete 不算孤儿(本来就是删除标记)。
    """
    sc = SyncChange
    cat = UserCategoryProjection
    acc = UserAccountProjection
    tag = UserTagProjection
    tx = ReadTxProjection
    bud = ReadBudgetProjection
    # 跳过 entity_type 不在已知集合内的(legacy / 未识别)
    stmt = (
        select(sc.change_id, sc.user_id, sc.entity_type, sc.entity_sync_id, sc.action)
        .where(sc.action != "delete")
        .where(
            or_(
                # transaction
                (sc.entity_type == "transaction") & ~select(literal_column("1"))
                .where(tx.user_id == sc.user_id, tx.sync_id == sc.entity_sync_id)
                .select_from(tx).exists(),
                # account
                (sc.entity_type == "account") & ~select(literal_column("1"))
                .where(acc.user_id == sc.user_id, acc.sync_id == sc.entity_sync_id)
                .select_from(acc).exists(),
                # category
                (sc.entity_type == "category") & ~select(literal_column("1"))
                .where(cat.user_id == sc.user_id, cat.sync_id == sc.entity_sync_id)
                .select_from(cat).exists(),
                # tag
                (sc.entity_type == "tag") & ~select(literal_column("1"))
                .where(tag.user_id == sc.user_id, tag.sync_id == sc.entity_sync_id)
                .select_from(tag).exists(),
                # budget
                (sc.entity_type == "budget") & ~select(literal_column("1"))
                .where(bud.user_id == sc.user_id, bud.sync_id == sc.entity_sync_id)
                .select_from(bud).exists(),
            )
        )
    )
    return [
        OrphanRecord(
            type=OrphanType.SYNC_CHANGE_MISSING_ENTITY,
            user_id=row.user_id,
            row_id=str(row.change_id),
            sync_id=row.entity_sync_id,
            title=f"SyncChange #{row.change_id}",
            subtitle=f"{row.entity_type} · {row.action} · 实体已删 {row.entity_sync_id[:8]}…",
            extra={"change_id": row.change_id, "entity_type": row.entity_type},
        )
        for row in db.execute(stmt).all()
    ]


# ─────────────────────── B 类:附件/文件 ───────────────────────


def _scan_attachment_no_ref(db: Session) -> list[OrphanRecord]:
    """B1 — AttachmentFile 行没被任何 tx (attachments_json) 或 category
    (icon_cloud_file_id) 引用。

    复用 [_fileid_still_referenced][src.projection._fileid_still_referenced]
    的判定逻辑,但这里逐行扫,慢但简单(admin 用,可接受)。
    """
    from ...projection import _fileid_still_referenced  # 避免循环 import

    result: list[OrphanRecord] = []
    rows = db.execute(
        select(
            AttachmentFile.id,
            AttachmentFile.user_id,
            AttachmentFile.size_bytes,
            AttachmentFile.file_name,
            AttachmentFile.storage_path,
            AttachmentFile.attachment_kind,
        )
    ).all()
    for row in rows:
        if _fileid_still_referenced(db, user_id=row.user_id, file_id=row.id):
            continue
        result.append(
            OrphanRecord(
                type=OrphanType.ATTACHMENT_NO_REF,
                user_id=row.user_id,
                row_id=row.id,
                title=row.file_name or row.id[:12],
                subtitle=f"附件无引用 · {row.attachment_kind}",
                file_path=row.storage_path,
                size_bytes=row.size_bytes,
            )
        )
    return result


def _scan_attachment_file_missing(db: Session) -> list[OrphanRecord]:
    """B2 — AttachmentFile.storage_path 指向的物理文件不存在。"""
    rows = db.execute(
        select(
            AttachmentFile.id,
            AttachmentFile.user_id,
            AttachmentFile.file_name,
            AttachmentFile.storage_path,
            AttachmentFile.size_bytes,
        )
    ).all()
    result: list[OrphanRecord] = []
    for row in rows:
        if not row.storage_path:
            continue
        if os.path.exists(row.storage_path):
            continue
        result.append(
            OrphanRecord(
                type=OrphanType.ATTACHMENT_FILE_MISSING,
                user_id=row.user_id,
                row_id=row.id,
                title=row.file_name or row.id[:12],
                subtitle="磁盘文件丢失,DB 行残留",
                file_path=row.storage_path,
                size_bytes=row.size_bytes,
            )
        )
    return result


def _scan_disk_file_no_row(db: Session, root: Path) -> list[OrphanRecord]:
    """B3 — 磁盘 attachments 目录下文件不在 attachment_files 表。

    跨用户递归扫,根目录结构是 `<root>/<user_id>/<ledger_id>/<sha2>/<storage_name>`
    或 `<root>/<user_id>/category-icons/<sha2>/<storage_name>`。比对方式:把
    attachment_files.storage_path 全集塞 set,然后 walk 磁盘找差集。
    """
    if not root.exists():
        return []
    db_paths = {
        row.storage_path
        for row in db.execute(select(AttachmentFile.storage_path)).all()
        if row.storage_path
    }
    result: list[OrphanRecord] = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            full = os.path.join(dirpath, f)
            if full in db_paths:
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                size = None
            result.append(
                OrphanRecord(
                    type=OrphanType.DISK_FILE_NO_ROW,
                    title=f,
                    subtitle=f"磁盘文件无 DB 行 · {os.path.relpath(full, root)}",
                    file_path=full,
                    size_bytes=size,
                )
            )
    return result


def _scan_tx_ref_broken_attachment(db: Session) -> list[OrphanRecord]:
    """B4 — read_tx_projection.attachments_json 引用的 cloudFileId 在
    attachment_files 不存在。

    解析 JSON 拿全部 cloudFileId,跟 AttachmentFile.id 集合做差。
    """
    all_file_ids = {
        row[0]
        for row in db.execute(select(AttachmentFile.id)).all()
    }
    rows = db.execute(
        select(
            ReadTxProjection.ledger_id,
            ReadTxProjection.sync_id,
            ReadTxProjection.user_id,
            ReadTxProjection.attachments_json,
        ).where(ReadTxProjection.attachments_json.isnot(None))
    ).all()
    result: list[OrphanRecord] = []
    for row in rows:
        if not row.attachments_json:
            continue
        try:
            atts = json.loads(row.attachments_json)
        except (ValueError, TypeError):
            continue
        if not isinstance(atts, list):
            continue
        broken: list[str] = []
        for att in atts:
            if not isinstance(att, dict):
                continue
            fid = att.get("cloudFileId")
            if isinstance(fid, str) and fid and fid not in all_file_ids:
                broken.append(fid)
        if not broken:
            continue
        result.append(
            OrphanRecord(
                type=OrphanType.TX_REF_BROKEN_ATTACHMENT,
                user_id=row.user_id,
                row_id=f"{row.ledger_id}:{row.sync_id}",
                sync_id=row.sync_id,
                title=f"交易 {row.sync_id[:8]} 引用 {len(broken)} 个失效附件",
                subtitle=f"cloudFileId 不在 attachment_files 表:{broken[0][:12]}…",
                extra={
                    "ledger_id": row.ledger_id,
                    "sync_id": row.sync_id,
                    "broken_file_ids": broken,
                },
            )
        )
    return result


# 防 lint 不用
_unused = (aliased, Iterable)
