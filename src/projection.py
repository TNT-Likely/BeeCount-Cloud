"""CQRS Q-side projection writers.

snapshot 是权威源。每次 materialize / diff emit 都在**同事务**内把对应的实体
upsert / delete 到这里的 read_*_projection 表。web `/read/*` 路径只查这些表,
不再 parse 3MB 的 ledger_snapshot JSON。

所有函数都只做"按入参写库",不读 snapshot、不关心上下文 —— 上层调用方(sync /
write / admin)负责把正确的 payload 字段拆出来传进来。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .models import (
    AttachmentFile,
    Ledger,
    ReadBudgetProjection,
    ReadTxProjection,
    UserAccountProjection,
    UserCategoryProjection,
    UserTagProjection,
)

logger = logging.getLogger(__name__)


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

    # Upsert 前抓 prev 附件 fileIds,跟 new 做 diff 找到被移除的那些。
    # 覆盖"一张交易有 N 个附件,只删掉其中一个"的场景 —— 老逻辑只管写新的
    # attachments_json,没清理从列表里被剔除的 AttachmentFile 行 + 物理文件。
    prev_file_ids = collect_tx_attachment_fileids(
        db, ledger_id=ledger_id, sync_id=sync_id
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

    # 新行已落地,对 prev - new 的 fileId 查还有无引用 → GC。
    # gc_orphan_attachments 契约要求新行就位后再调,这里顺序正确。
    new_file_ids = _extract_tx_cloud_file_ids(attachments_json)
    removed = prev_file_ids - new_file_ids
    if removed:
        gc_orphan_attachments(db, user_id=user_id, file_ids=removed)


def upsert_account(
    db: Session,
    *,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """user-global account upsert。PK=(user_id, sync_id),跟账本无关。"""
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return

    # 扩展字段读 snapshot 的 camelCase key,空 / NaN 转 None。这些字段是
    # nullable 的(老 snapshot 没这些 key 时落 None,前端展示空)。
    def _opt_float(raw: Any) -> float | None:
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def _opt_int(raw: Any) -> int | None:
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    values = {
        "user_id": user_id,
        "sync_id": sync_id,
        "name": _as_str(payload.get("name")),
        "account_type": _as_str(payload.get("type")),
        "currency": _as_str(payload.get("currency")),
        "initial_balance": _as_float(payload.get("initialBalance")),
        "note": _as_str(payload.get("note")),
        "credit_limit": _opt_float(payload.get("creditLimit")),
        "billing_day": _opt_int(payload.get("billingDay")),
        "payment_due_day": _opt_int(payload.get("paymentDueDay")),
        "bank_name": _as_str(payload.get("bankName")),
        "card_last_four": _as_str(payload.get("cardLastFour")),
        "source_change_id": source_change_id,
    }
    _upsert(db, UserAccountProjection, ("user_id", "sync_id"), values)


def upsert_category(
    db: Session,
    *,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """user-global category upsert。PK=(user_id, sync_id),跟账本无关。"""
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return

    # 取 prev icon_cloud_file_id —— upsert 后跟 new 比对,若变更则 GC 旧 attachment。
    # 防止用户在 web 点 "remove 自定义图标" 后旧 cloud blob 永远成孤儿。
    prev_icon_file_id: str | None = None
    prev_row = db.scalar(
        select(UserCategoryProjection.icon_cloud_file_id).where(
            UserCategoryProjection.user_id == user_id,
            UserCategoryProjection.sync_id == sync_id,
        )
    )
    if prev_row:
        prev_icon_file_id = prev_row.strip() if isinstance(prev_row, str) else None

    values = {
        "user_id": user_id,
        "sync_id": sync_id,
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
    _upsert(db, UserCategoryProjection, ("user_id", "sync_id"), values)

    # 如果 prev_icon_file_id 跟新值不一样(包括清空、换图),且 prev 不是空,
    # 把旧 fileId 交给 gc_orphan_attachments —— 真孤儿才删 AttachmentFile +
    # 物理文件,被共用的会留着。
    new_icon_file_id = _as_str(payload.get("iconCloudFileId")) or None
    if prev_icon_file_id and prev_icon_file_id != new_icon_file_id:
        gc_orphan_attachments(db, user_id=user_id, file_ids=[prev_icon_file_id])


def upsert_tag(
    db: Session,
    *,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """user-global tag upsert。PK=(user_id, sync_id),跟账本无关。"""
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    values = {
        "user_id": user_id,
        "sync_id": sync_id,
        "name": _as_str(payload.get("name")),
        "color": _as_str(payload.get("color")),
        "source_change_id": source_change_id,
    }
    _upsert(db, UserTagProjection, ("user_id", "sync_id"), values)


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


def delete_account(db: Session, *, user_id: str, sync_id: str) -> None:
    """user-global account delete。PK=(user_id, sync_id)。"""
    db.execute(
        delete(UserAccountProjection).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.sync_id == sync_id,
        )
    )


def delete_category(db: Session, *, user_id: str, sync_id: str) -> None:
    """user-global category delete。PK=(user_id, sync_id)。"""
    db.execute(
        delete(UserCategoryProjection).where(
            UserCategoryProjection.user_id == user_id,
            UserCategoryProjection.sync_id == sync_id,
        )
    )


def delete_tag(db: Session, *, user_id: str, sync_id: str) -> None:
    """user-global tag delete。PK=(user_id, sync_id)。"""
    db.execute(
        delete(UserTagProjection).where(
            UserTagProjection.user_id == user_id,
            UserTagProjection.sync_id == sync_id,
        )
    )


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
    user_id: str,
    account_sync_id: str,
    new_name: str | None,
) -> None:
    """account 是 user-global,rename 时刷遍该用户所有 ledger 的 read_tx_projection。"""
    from sqlalchemy import update

    # 一次 UPDATE 用 user_id 圈定范围;不再循环 ledger。
    db.execute(
        update(ReadTxProjection)
        .where(
            ReadTxProjection.user_id == user_id,
            ReadTxProjection.account_sync_id == account_sync_id,
        )
        .values(account_name=new_name)
    )
    db.execute(
        update(ReadTxProjection)
        .where(
            ReadTxProjection.user_id == user_id,
            ReadTxProjection.from_account_sync_id == account_sync_id,
        )
        .values(from_account_name=new_name)
    )
    db.execute(
        update(ReadTxProjection)
        .where(
            ReadTxProjection.user_id == user_id,
            ReadTxProjection.to_account_sync_id == account_sync_id,
        )
        .values(to_account_name=new_name)
    )


def rename_cascade_category(
    db: Session,
    *,
    user_id: str,
    category_sync_id: str,
    new_name: str | None,
    new_kind: str | None = None,
) -> None:
    """category 是 user-global,rename 时刷遍该用户所有 ledger 的 read_tx_projection。"""
    from sqlalchemy import update

    values: dict[str, Any] = {"category_name": new_name}
    if new_kind is not None:
        values["category_kind"] = new_kind
    db.execute(
        update(ReadTxProjection)
        .where(
            ReadTxProjection.user_id == user_id,
            ReadTxProjection.category_sync_id == category_sync_id,
        )
        .values(**values)
    )


def rename_cascade_tag(
    db: Session,
    *,
    user_id: str,
    tag_sync_id: str,
    old_name: str,
    new_name: str,
) -> None:
    """Tag rename 走 tags_csv 字符串替换。tag 是 user-global,刷该用户所有 tx 行。
    用 Python 做字符串替换比纯 SQL 的 REPLACE 更安全(避免 name 是别的 tag 的
    substring 时误伤)。
    """
    from sqlalchemy import select as sql_select

    if not old_name or not new_name or old_name == new_name:
        return
    # tag_sync_ids_json 存的是 `["tag_a", "tag_b"]`,LIKE 匹配 "tag_a" 能框住
    like_pat = f'%"{tag_sync_id}"%'
    rows = db.scalars(
        sql_select(ReadTxProjection).where(
            ReadTxProjection.user_id == user_id,
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
    """清掉该 ledger 的 ledger-scoped projection。user-global(account/
    category/tag)是 per-user 表,不按 ledger 清,**不在此处处理**。"""
    for model in (ReadTxProjection, ReadBudgetProjection):
        db.execute(delete(model).where(model.ledger_id == ledger_id))


def rebuild_from_snapshot(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    snapshot: dict[str, Any],
    source_change_id: int,
) -> None:
    """按 snapshot 权威源,把该 ledger 的 ledger-scoped projection 清零再填。
    user-global(account/category/tag)是 per-user 表,**upsert 不清空** —— 同
    user 跨 ledger rebuild 多次也是幂等的(同 sync_id 的 row 被相同值覆盖)。

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
                user_id=user_id,
                source_change_id=source_change_id,
                payload=item,
            )
    for item in snapshot.get("categories") or []:
        if isinstance(item, dict):
            upsert_category(
                db,
                user_id=user_id,
                source_change_id=source_change_id,
                payload=item,
            )
    for item in snapshot.get("tags") or []:
        if isinstance(item, dict):
            upsert_tag(
                db,
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


# --------------------------------------------------------------------------- #
# AttachmentFile 垃圾回收                                                       #
# --------------------------------------------------------------------------- #
# AttachmentFile 表跟 tx / category 没有 FK —— 共享池 + sha256 去重,允许一个
# blob 被多个 tx(截图上传同图)或 category 图标同时引用。所以删 tx/category
# 时不能盲目删 AttachmentFile,必须查"还有没有其他实体引用同 fileId"。
#
# 反向引用只存在于:
#   - read_tx_projection.attachments_json  (JSON list,每项含 cloudFileId)
#   - read_category_projection.icon_cloud_file_id  (单列)
# 0 引用就 DELETE 行 + unlink 物理文件(best-effort,磁盘 IO 失败只 warn)。


def _extract_tx_cloud_file_ids(attachments_json: str | None) -> set[str]:
    """从 tx projection 的 attachments_json 里抽出所有 cloudFileId。

    mobile 序列化(`lib/cloud/sync/sync_engine.dart:802-812`) + web 写路径都用
    `cloudFileId` 这个 camelCase key。JSON 缺失 / 解析失败 / 非 list → 空 set。
    """
    if not attachments_json:
        return set()
    try:
        arr = json.loads(attachments_json)
    except json.JSONDecodeError:
        return set()
    if not isinstance(arr, list):
        return set()
    out: set[str] = set()
    for item in arr:
        if isinstance(item, dict):
            fid = item.get("cloudFileId")
            if isinstance(fid, str) and fid.strip():
                out.add(fid.strip())
    return out


def _fileid_still_referenced(db: Session, *, user_id: str, file_id: str) -> bool:
    """某个 AttachmentFile 在该用户的 projection 里还有引用吗?

    扫:
      - read_tx_projection.attachments_json LIKE '%"cloudFileId":"<id>"%' (JSON
        字段名是 cloudFileId)。两种空格变体都 match,防备 client / server 序列
        化习惯差异。tx 是 ledger-scoped,但带 user_id denorm 列。
      - user_category_projection.icon_cloud_file_id = <id>(per-user 表)
    """
    pat_no_space = f'%"cloudFileId":"{file_id}"%'
    pat_with_space = f'%"cloudFileId": "{file_id}"%'
    tx_hit = db.scalar(
        select(func.count())
        .select_from(ReadTxProjection)
        .where(
            ReadTxProjection.user_id == user_id,
            or_(
                ReadTxProjection.attachments_json.like(pat_no_space),
                ReadTxProjection.attachments_json.like(pat_with_space),
            ),
        )
    )
    if tx_hit:
        return True

    cat_hit = db.scalar(
        select(func.count())
        .select_from(UserCategoryProjection)
        .where(
            UserCategoryProjection.user_id == user_id,
            UserCategoryProjection.icon_cloud_file_id == file_id,
        )
    )
    return bool(cat_hit)


def gc_orphan_attachments(
    db: Session,
    *,
    user_id: str,
    file_ids: Iterable[str | None],
) -> int:
    """对给定 fileId 集合,若该用户 projection 里无任何引用 → 删 AttachmentFile
    行 + unlink 物理文件。返回实际清掉的条数(日志/测试用)。

    **调用契约**:必须在目标 tx/category 的 projection 行**已经删掉**之后调用。
    否则会把正在用的 blob 误删。事务边界由调用方管(两个修改应在同一事务 commit)。

    Scope 到单 user:AttachmentFile 自身带 user_id,引用检查也限到同 user
    (跨 ledger 的 tx 都看,因为 tx 用 user_id denorm 列;category 是 per-user)。
    比老版本"按 ledger_id 过滤"更准:category_icon 的 AttachmentFile.ledger_id
    本来就是 NULL,老版本 GC 永远 miss 这类附件 —— 本次顺手修复。

    物理文件 unlink 失败只 warn 不抛 —— DB 行已删是事实,磁盘残留可后续清理
    脚本补扫。
    """
    cleaned = 0
    seen: set[str] = set()
    for fid in file_ids:
        if not fid:
            continue
        file_id = fid.strip()
        if not file_id or file_id in seen:
            continue
        seen.add(file_id)

        if _fileid_still_referenced(db, user_id=user_id, file_id=file_id):
            continue

        att = db.scalar(
            select(AttachmentFile).where(
                AttachmentFile.id == file_id,
                AttachmentFile.user_id == user_id,
            )
        )
        if att is None:
            # AttachmentFile 已经不存在(手动清过 / 历史脏数据)—— 不算失败
            continue

        storage_path = att.storage_path
        db.delete(att)

        # Best-effort unlink。DB 提交失败时这里改不回来,但事务 rollback 后
        # AttachmentFile 行还在,下次 GC 再来。
        try:
            p = Path(storage_path)
            if p.exists():
                p.unlink()
        except OSError as exc:
            logger.warning(
                "attachment gc unlink failed user=%s file=%s path=%s err=%s",
                user_id, file_id, storage_path, exc,
            )

        cleaned += 1

    if cleaned:
        logger.info(
            "attachment gc user=%s cleaned=%d", user_id, cleaned,
        )
    return cleaned


def collect_tx_attachment_fileids(
    db: Session, *, ledger_id: str, sync_id: str,
) -> set[str]:
    """在 tx projection 删之前取 attachments_json 里的 fileId 列表,供删后 GC 用。
    行不存在(已删 / 从未有过)→ 空 set。"""
    row = db.scalar(
        select(ReadTxProjection.attachments_json).where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.sync_id == sync_id,
        )
    )
    return _extract_tx_cloud_file_ids(row)


def collect_category_icon_fileids(
    db: Session, *, user_id: str, sync_id: str,
) -> set[str]:
    """在 user_category_projection 删之前取 icon_cloud_file_id + 所有
    parent=sync_id 子分类的 icon_cloud_file_id。级联删父分类时会同步带走子分类。
    category 是 user-global,scope 改成 user_id。"""
    out: set[str] = set()
    # 自身
    self_icon = db.scalar(
        select(UserCategoryProjection.icon_cloud_file_id).where(
            UserCategoryProjection.user_id == user_id,
            UserCategoryProjection.sync_id == sync_id,
        )
    )
    if isinstance(self_icon, str) and self_icon.strip():
        out.add(self_icon.strip())
    # 子分类(parent_name 匹配本分类的 name —— projection 里 parent 关系由 name
    # 串起来,见 upsert_category 的 parent_name 字段)。这里不走级联删,仅提取
    # icon。调用方如果要真级联删子分类,另外安排。
    own_name = db.scalar(
        select(UserCategoryProjection.name).where(
            UserCategoryProjection.user_id == user_id,
            UserCategoryProjection.sync_id == sync_id,
        )
    )
    if isinstance(own_name, str) and own_name:
        child_icons = db.scalars(
            select(UserCategoryProjection.icon_cloud_file_id).where(
                UserCategoryProjection.user_id == user_id,
                UserCategoryProjection.parent_name == own_name,
            )
        ).all()
        for icon in child_icons:
            if isinstance(icon, str) and icon.strip():
                out.add(icon.strip())
    return out
