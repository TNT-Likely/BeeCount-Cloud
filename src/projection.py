"""CQRS Q-side projection writers.

snapshot 是权威源。每次 materialize / diff emit 都在**同事务**内把对应的实体
upsert / delete 到这里的 read_*_projection 表。web `/read/*` 路径只查这些表,
不再 parse 3MB 的 ledger_snapshot JSON。

所有函数都只做"按入参写库",不读 snapshot、不关心上下文 —— 上层调用方(sync /
write / admin)负责把正确的 payload 字段拆出来传进来。
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .models import (
    ReadAccountProjection,
    ReadBudgetProjection,
    ReadCategoryProjection,
    ReadTagProjection,
    ReadTxProjection,
)


# --------------------------------------------------------------------------- #
# Payload 字段提取                                                              #
# --------------------------------------------------------------------------- #
# snapshot items 里的 key 是 camelCase(mobile Flutter 友好);projection 列
# 是 snake_case。这些 helper 把两边对齐。入参宽松:None / 空字符串 / 缺失都
# 当作 None 处理,不抛。

def _as_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _as_float(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _as_int(v: Any, default: int = 0) -> int:
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_bool(v: Any, default: bool = True) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"true", "1", "yes", "y", "t"}
    return default


def _parse_happened_at(raw: Any):
    """happenedAt 通常是 ISO 8601 字符串,偶见 datetime 对象。"""
    from datetime import datetime, timezone

    if raw is None:
        return datetime.now(timezone.utc)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return datetime.now(timezone.utc)
        # Python 3.11+ fromisoformat 吃 "Z" 结尾
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Dialect 中立的 upsert                                                         #
# --------------------------------------------------------------------------- #
# SQLite / PostgreSQL 都用 INSERT ... ON CONFLICT DO UPDATE。SQLAlchemy 的
# `dialects.sqlite.insert` 在两种库上语法基本一致;`dialects.postgresql.insert`
# 同理。我们按 bind 方言走对应 insert,fallback 到先 SELECT 再 UPDATE/INSERT。

def _is_sqlite(bind) -> bool:
    try:
        name = bind.dialect.name if hasattr(bind, "dialect") else bind.bind.dialect.name
    except AttributeError:
        return True
    return name == "sqlite"


def _upsert(db: Session, model, pk_fields: tuple[str, ...], values: dict) -> None:
    """通用 upsert:主键撞了就 UPDATE 其他所有列。"""
    bind = db.get_bind()
    if _is_sqlite(bind) or getattr(bind.dialect, "name", "") == "postgresql":
        # SQLite / PG 都支持 ON CONFLICT。这里用 sqlite 方言 insert 生成语句,
        # 实际执行时由 SQLAlchemy 翻译;PG 下走一样的语义。
        stmt = sqlite_insert(model).values(**values)
        update_cols = {k: stmt.excluded[k] for k in values.keys() if k not in pk_fields}
        if update_cols:
            stmt = stmt.on_conflict_do_update(
                index_elements=list(pk_fields), set_=update_cols
            )
        else:
            # 没有非主键列要改(理论不会发生),退化成 DO NOTHING
            stmt = stmt.on_conflict_do_nothing(index_elements=list(pk_fields))
        db.execute(stmt)
        return

    # 兜底:未知方言用 merge 风格(select → insert or update)
    filters = [getattr(model, k) == values[k] for k in pk_fields]
    existing = db.scalar(select(model).where(*filters))
    if existing is None:
        db.add(model(**values))
    else:
        for k, v in values.items():
            if k not in pk_fields:
                setattr(existing, k, v)


# --------------------------------------------------------------------------- #
# 单实体:upsert / delete                                                       #
# --------------------------------------------------------------------------- #

def upsert_tx(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    tags_raw = payload.get("tags")
    if isinstance(tags_raw, list):
        tags_csv = ",".join(str(t).strip() for t in tags_raw if str(t).strip())
    else:
        tags_csv = _as_str(tags_raw)

    tag_sync_ids = payload.get("tagIds")
    tag_sync_ids_json = json.dumps(tag_sync_ids) if isinstance(tag_sync_ids, list) else None

    attachments = payload.get("attachments")
    attachments_json = (
        json.dumps(attachments) if isinstance(attachments, list) and attachments else None
    )

    tx_type = (
        _as_str(payload.get("txType"))
        or _as_str(payload.get("tx_type"))
        or _as_str(payload.get("type"))
        or "expense"
    )

    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "tx_type": tx_type,
        "amount": _as_float(payload.get("amount")),
        "happened_at": _parse_happened_at(
            payload.get("happenedAt") or payload.get("happened_at")
        ),
        "note": _as_str(payload.get("note")),
        "category_sync_id": _as_str(payload.get("categoryId")),
        "category_name": _as_str(payload.get("categoryName")),
        "category_kind": _as_str(payload.get("categoryKind")),
        "account_sync_id": _as_str(payload.get("accountId")),
        "account_name": _as_str(payload.get("accountName")),
        "from_account_sync_id": _as_str(payload.get("fromAccountId")),
        "from_account_name": _as_str(payload.get("fromAccountName")),
        "to_account_sync_id": _as_str(payload.get("toAccountId")),
        "to_account_name": _as_str(payload.get("toAccountName")),
        "tags_csv": tags_csv,
        "tag_sync_ids_json": tag_sync_ids_json,
        "attachments_json": attachments_json,
        "tx_index": _as_int(payload.get("txIndex") or payload.get("tx_index"), default=0),
        "created_by_user_id": _as_str(payload.get("createdByUserId")),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadTxProjection, ("ledger_id", "sync_id"), values)


def upsert_account(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "name": _as_str(payload.get("name")),
        "account_type": _as_str(payload.get("type")),
        "currency": _as_str(payload.get("currency")),
        "initial_balance": _as_float(payload.get("initialBalance")),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadAccountProjection, ("ledger_id", "sync_id"), values)


def upsert_category(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "name": _as_str(payload.get("name")),
        "kind": _as_str(payload.get("kind")),
        "level": _as_int(payload.get("level"), default=0) if payload.get("level") is not None else None,
        "sort_order": _as_int(payload.get("sortOrder"), default=0)
        if payload.get("sortOrder") is not None
        else None,
        "icon": _as_str(payload.get("icon")),
        "icon_type": _as_str(payload.get("iconType")),
        "custom_icon_path": _as_str(payload.get("customIconPath")),
        "icon_cloud_file_id": _as_str(payload.get("iconCloudFileId")),
        "icon_cloud_sha256": _as_str(payload.get("iconCloudSha256")),
        "parent_name": _as_str(payload.get("parentName")),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadCategoryProjection, ("ledger_id", "sync_id"), values)


def upsert_tag(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "name": _as_str(payload.get("name")),
        "color": _as_str(payload.get("color")),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadTagProjection, ("ledger_id", "sync_id"), values)


def upsert_budget(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "budget_type": _as_str(payload.get("type")),
        "category_sync_id": _as_str(payload.get("categoryId")),
        "amount": _as_float(payload.get("amount")) if payload.get("amount") is not None else None,
        "period": _as_str(payload.get("period")),
        "start_day": _as_int(payload.get("startDay"), default=1)
        if payload.get("startDay") is not None
        else None,
        "enabled": _as_bool(payload.get("enabled"), default=True),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadBudgetProjection, ("ledger_id", "sync_id"), values)


def delete_entity(
    db: Session, model, *, ledger_id: str, sync_id: str
) -> None:
    db.execute(
        delete(model).where(
            model.ledger_id == ledger_id,
            model.sync_id == sync_id,
        )
    )


def delete_tx(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadTxProjection, ledger_id=ledger_id, sync_id=sync_id)


def delete_account(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadAccountProjection, ledger_id=ledger_id, sync_id=sync_id)


def delete_category(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadCategoryProjection, ledger_id=ledger_id, sync_id=sync_id)


def delete_tag(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadTagProjection, ledger_id=ledger_id, sync_id=sync_id)


def delete_budget(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadBudgetProjection, ledger_id=ledger_id, sync_id=sync_id)


# --------------------------------------------------------------------------- #
# Rename cascade                                                               #
# --------------------------------------------------------------------------- #
# 当 account / category / tag 的 name 改了,tx projection 里引用它的 denorm
# 列也要同步更新。snapshot 里已经有同样 cascade 逻辑(见
# sync.py._materialize_individual_changes),这里做对应 SQL。

def rename_cascade_account(
    db: Session,
    *,
    ledger_id: str,
    account_sync_id: str,
    new_name: str | None,
) -> None:
    from sqlalchemy import update

    # account_name / fromAccountName / toAccountName 三个引用点都要刷
    db.execute(
        update(ReadTxProjection)
        .where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.account_sync_id == account_sync_id,
        )
        .values(account_name=new_name)
    )
    db.execute(
        update(ReadTxProjection)
        .where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.from_account_sync_id == account_sync_id,
        )
        .values(from_account_name=new_name)
    )
    db.execute(
        update(ReadTxProjection)
        .where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.to_account_sync_id == account_sync_id,
        )
        .values(to_account_name=new_name)
    )


def rename_cascade_category(
    db: Session,
    *,
    ledger_id: str,
    category_sync_id: str,
    new_name: str | None,
    new_kind: str | None = None,
) -> None:
    from sqlalchemy import update

    values: dict[str, Any] = {"category_name": new_name}
    if new_kind is not None:
        values["category_kind"] = new_kind
    db.execute(
        update(ReadTxProjection)
        .where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.category_sync_id == category_sync_id,
        )
        .values(**values)
    )


def rename_cascade_tag(
    db: Session,
    *,
    ledger_id: str,
    tag_sync_id: str,
    old_name: str,
    new_name: str,
) -> None:
    """Tag rename 走 tags_csv 字符串替换。
    snapshot 里的逻辑是按 tagIds 列表精确定位,我们这里没有直接按 tag_sync_id
    查 tx 的索引,但可以通过 tag_sync_ids_json 走 LIKE 查到涉及的 tx,再更新
    tags_csv。用 Python 做字符串替换比纯 SQL 的 REPLACE 更安全(避免 name
    是别的 tag 的 substring 时误伤)。
    """
    from sqlalchemy import select as sql_select

    if not old_name or not new_name or old_name == new_name:
        return
    # tag_sync_ids_json 存的是 `["tag_a", "tag_b"]`,LIKE 匹配 "tag_a" 能框住
    like_pat = f'%"{tag_sync_id}"%'
    rows = db.scalars(
        sql_select(ReadTxProjection).where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.tag_sync_ids_json.like(like_pat),
        )
    ).all()
    for row in rows:
        if not row.tags_csv:
            continue
        parts = [p.strip() for p in row.tags_csv.split(",") if p.strip()]
        replaced = [new_name if p == old_name else p for p in parts]
        if replaced != parts:
            row.tags_csv = ",".join(replaced)


# --------------------------------------------------------------------------- #
# 整表重建:回填 / 恢复备份                                                     #
# --------------------------------------------------------------------------- #

def _truncate_ledger(db: Session, ledger_id: str) -> None:
    for model in (
        ReadTxProjection,
        ReadAccountProjection,
        ReadCategoryProjection,
        ReadTagProjection,
        ReadBudgetProjection,
    ):
        db.execute(delete(model).where(model.ledger_id == ledger_id))


def rebuild_from_snapshot(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    snapshot: dict[str, Any],
    source_change_id: int,
) -> None:
    """按 snapshot 权威源,把该 ledger 的 5 张 projection 清零再填一遍。
    用于:alembic 回填、admin restore_backup、脏数据救急脚本。
    """
    _truncate_ledger(db, ledger_id)

    for item in snapshot.get("items") or []:
        if isinstance(item, dict):
            upsert_tx(
                db,
                ledger_id=ledger_id,
                user_id=user_id,
                source_change_id=source_change_id,
                payload=item,
            )
    for item in snapshot.get("accounts") or []:
        if isinstance(item, dict):
            upsert_account(
                db,
                ledger_id=ledger_id,
                user_id=user_id,
                source_change_id=source_change_id,
                payload=item,
            )
    for item in snapshot.get("categories") or []:
        if isinstance(item, dict):
            upsert_category(
                db,
                ledger_id=ledger_id,
                user_id=user_id,
                source_change_id=source_change_id,
                payload=item,
            )
    for item in snapshot.get("tags") or []:
        if isinstance(item, dict):
            upsert_tag(
                db,
                ledger_id=ledger_id,
                user_id=user_id,
                source_change_id=source_change_id,
                payload=item,
            )
    for item in snapshot.get("budgets") or []:
        if isinstance(item, dict):
            upsert_budget(
                db,
                ledger_id=ledger_id,
                user_id=user_id,
                source_change_id=source_change_id,
                payload=item,
            )


def rebuild_all(db: Session) -> int:
    """遍历所有 ledger,按各自 latest snapshot 重建 projection。
    返回处理的 ledger 个数。救急脚本 `scripts/rebuild_all_projections.py` 用。
    """
    from sqlalchemy import func

    from .models import Ledger, SyncChange

    count = 0
    ledger_rows = db.execute(
        select(Ledger.id, Ledger.user_id)
    ).all()
    for ledger_id, user_id in ledger_rows:
        latest = db.scalar(
            select(SyncChange)
            .where(
                SyncChange.ledger_id == ledger_id,
                SyncChange.entity_type == "ledger_snapshot",
            )
            .order_by(SyncChange.change_id.desc())
            .limit(1)
        )
        if latest is None:
            continue
        payload = latest.payload_json
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            continue
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        try:
            snapshot = json.loads(content)
        except json.JSONDecodeError:
            continue
        if not isinstance(snapshot, dict):
            continue
        rebuild_from_snapshot(
            db,
            ledger_id=ledger_id,
            user_id=user_id,
            snapshot=snapshot,
            source_change_id=int(latest.change_id),
        )
        count += 1
    db.commit()
    return count
