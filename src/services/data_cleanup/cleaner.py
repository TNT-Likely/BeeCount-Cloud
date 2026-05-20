"""孤儿数据清理 — 按 type dispatch 删行 / 改字段 / 删文件。

DB 操作 caller 保证事务边界(router 层 commit / rollback);文件操作 best-effort
unlink,失败 warn。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterable

from sqlalchemy import update
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import (
    AttachmentFile,
    ReadBudgetProjection,
    ReadTxProjection,
    SyncChange,
)
from .models import CleanFailure, CleanResult, OrphanRecord, OrphanType

logger = logging.getLogger("beecount.data_cleanup")


def _storage_root() -> Path:
    """attachment_storage_dir 解析为绝对路径,用作 rmdir 向上递归的 stop_at。"""
    return Path(get_settings().attachment_storage_dir).expanduser().resolve()


def _remove_empty_parents(file_path: str, stop_at: Path | None = None) -> None:
    """删完一个附件文件后,向上递归删空目录,直到遇到非空目录 / stop_at / 根。

    stop_at 默认是 attachment_storage_dir 根 —— 永远不删根本身。每级用 rmdir
    (只在空时成功),非空会抛 OSError 被 catch,不会误删兄弟附件所在目录。
    """
    if not file_path:
        return
    try:
        root = (stop_at or _storage_root()).resolve()
    except OSError:
        return
    try:
        parent = Path(file_path).resolve().parent
    except OSError:
        return
    while True:
        # 必须严格在 root 之下,且不是 root 本身
        try:
            parent_resolved = parent.resolve()
        except OSError:
            return
        if parent_resolved == root:
            return
        try:
            parent_resolved.relative_to(root)
        except ValueError:
            # parent 已经在 root 之外 —— 安全起见停止
            return
        try:
            parent_resolved.rmdir()
        except OSError:
            # 非空 / 权限等任何错误 → 停止递归(兄弟附件还在,不再继续向上)
            return
        parent = parent_resolved.parent


def clean(db: Session, records: Iterable[OrphanRecord]) -> CleanResult:
    """逐条 dispatch 删除。失败收集到 failures,不阻断其余。

    caller 负责 commit / rollback。文件删失败只 warn。
    生产环境 SQLite 用,每条 record 处理完立即 db.commit() 避免长事务持有
    write lock 阻塞其他请求(observability 中间件的读请求频繁,长事务 + 文件
    IO 慢操作会导致 "database is locked")。文件 IO(unlink + rmdir)放到
    DB commit 之后,确保 DB lock 已释放。
    """
    success = 0
    failures: list[CleanFailure] = []
    for record in records:
        pending_file_ops: list[callable] = []  # type: ignore[type-arg]
        try:
            _dispatch(db, record, pending_file_ops)
            db.commit()
            success += 1
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.warning(
                "clean record %s db failed: %s",
                record.unique_key,
                exc,
            )
            failures.append(CleanFailure(record_key=record.unique_key, error=str(exc)))
            continue
        # DB 已 commit + 锁已释放 → 执行文件 IO。失败只 warn,不回滚 DB(行已删
        # 是事实,磁盘残留下次 GC 会扫到)。
        for op in pending_file_ops:
            try:
                op()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "clean record %s file op failed: %s",
                    record.unique_key,
                    exc,
                )
    return CleanResult(success_count=success, failures=failures)


def _dispatch(db: Session, r: OrphanRecord, file_ops: list) -> None:
    """跑 record 类型对应的 DB 操作;文件 IO append 到 file_ops 列表,由
    caller 在 db.commit 之后跑(避免长事务持锁)。"""
    t = r.type
    if t == OrphanType.TX_MISSING_CATEGORY:
        _clear_tx_field(db, r, "category_sync_id", "category_name")
    elif t == OrphanType.TX_MISSING_ACCOUNT:
        _clear_tx_field(db, r, "account_sync_id", "account_name")
    elif t == OrphanType.TX_MISSING_FROM_ACCOUNT:
        _clear_tx_field(db, r, "from_account_sync_id", "from_account_name")
    elif t == OrphanType.TX_MISSING_TO_ACCOUNT:
        _clear_tx_field(db, r, "to_account_sync_id", "to_account_name")
    elif t == OrphanType.BUDGET_MISSING_CATEGORY:
        _clear_budget_category(db, r)
    elif t == OrphanType.SYNC_CHANGE_MISSING_ENTITY:
        _delete_sync_change(db, r)
    elif t == OrphanType.ATTACHMENT_NO_REF:
        _delete_attachment_with_file(db, r, file_ops)
    elif t == OrphanType.ATTACHMENT_FILE_MISSING:
        _delete_attachment_row_only(db, r)
    elif t == OrphanType.DISK_FILE_NO_ROW:
        file_ops.append(lambda: _delete_disk_file_only(r))
    elif t == OrphanType.TX_REF_BROKEN_ATTACHMENT:
        _strip_broken_attachments(db, r)
    else:  # pragma: no cover
        raise ValueError(f"unknown OrphanType: {t}")


# ─────────────────────── helpers ───────────────────────


def _ledger_sync_from_record(r: OrphanRecord) -> tuple[str, str]:
    """从 extra 解析 (ledger_id, sync_id),失败抛 ValueError。"""
    extra = r.extra or {}
    ledger_id = extra.get("ledger_id")
    sync_id = extra.get("sync_id") or r.sync_id
    if not ledger_id or not sync_id:
        raise ValueError(f"record {r.unique_key} 缺 ledger_id/sync_id")
    return str(ledger_id), str(sync_id)


def _clear_tx_field(
    db: Session,
    r: OrphanRecord,
    sync_id_col: str,
    name_col: str,
) -> None:
    """A1/A2/A3:把 ReadTxProjection 的 *_sync_id 和 *_name 字段置 NULL。保留交易本体。"""
    ledger_id, sync_id = _ledger_sync_from_record(r)
    stmt = (
        update(ReadTxProjection)
        .where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.sync_id == sync_id,
        )
        .values(**{sync_id_col: None, name_col: None})
    )
    db.execute(stmt)


def _clear_budget_category(db: Session, r: OrphanRecord) -> None:
    """A4:ReadBudgetProjection.category_sync_id 置 NULL。"""
    ledger_id, sync_id = _ledger_sync_from_record(r)
    stmt = (
        update(ReadBudgetProjection)
        .where(
            ReadBudgetProjection.ledger_id == ledger_id,
            ReadBudgetProjection.sync_id == sync_id,
        )
        .values(category_sync_id=None)
    )
    db.execute(stmt)


def _delete_sync_change(db: Session, r: OrphanRecord) -> None:
    """A5/C1:删 sync_changes 行。"""
    if not r.row_id:
        raise ValueError("sync_change record 缺 row_id (change_id)")
    change_id = int(r.row_id)
    row = db.get(SyncChange, change_id)
    if row is None:
        return  # 已经被删,视为成功
    db.delete(row)


def _delete_attachment_with_file(
    db: Session, r: OrphanRecord, file_ops: list
) -> None:
    """B1:删 AttachmentFile 行(同事务)+ 物理文件 + 删空父目录(commit 后)。"""
    if not r.row_id:
        raise ValueError("attachment record 缺 row_id")
    row = db.get(AttachmentFile, r.row_id)
    if row is None:
        return
    storage_path = row.storage_path
    db.delete(row)
    if storage_path:
        def _do_file_op(path: str = storage_path, row_id: str = r.row_id) -> None:
            try:
                if os.path.exists(path):
                    os.remove(path)
                _remove_empty_parents(path)
            except OSError as exc:
                logger.warning(
                    "B1 unlink failed file=%s path=%s err=%s",
                    row_id, path, exc,
                )
        file_ops.append(_do_file_op)


def _delete_attachment_row_only(db: Session, r: OrphanRecord) -> None:
    """B2:磁盘已没,只删 DB 行。"""
    if not r.row_id:
        raise ValueError("attachment record 缺 row_id")
    row = db.get(AttachmentFile, r.row_id)
    if row is None:
        return
    db.delete(row)


def _delete_disk_file_only(r: OrphanRecord) -> None:
    """B3:DB 没记,只清磁盘 + 删空父目录。"""
    if not r.file_path:
        raise ValueError("disk file record 缺 file_path")
    if os.path.exists(r.file_path):
        os.remove(r.file_path)
    _remove_empty_parents(r.file_path)


def _strip_broken_attachments(db: Session, r: OrphanRecord) -> None:
    """B4:从 ReadTxProjection.attachments_json 移除指向不存在 fileId 的项。

    tx 本体保留 + 重写 JSON。broken_file_ids 从 extra 拿。
    """
    from sqlalchemy import select

    ledger_id, sync_id = _ledger_sync_from_record(r)
    broken = (r.extra or {}).get("broken_file_ids") or []
    if not broken:
        return
    broken_set = set(broken)
    obj = db.scalar(
        select(ReadTxProjection).where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.sync_id == sync_id,
        )
    )
    if obj is None or not obj.attachments_json:
        return
    try:
        atts = json.loads(obj.attachments_json)
    except (ValueError, TypeError):
        return
    if not isinstance(atts, list):
        return
    kept = [
        a
        for a in atts
        if not (isinstance(a, dict) and a.get("cloudFileId") in broken_set)
    ]
    obj.attachments_json = json.dumps(kept) if kept else None
