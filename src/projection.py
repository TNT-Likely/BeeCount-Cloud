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

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .models import (
    AttachmentFile,
    ReadBudgetProjection,
    ReadItemReturnProjection,
    ReadItemProjection,
    ReadLoanProjection,
    ReadMemberProjection,
    ReadReimbursementProjection,
    ReadRefundProjection,
    ReadTimeDepositProjection,
    ReadTxProjection,
    UserAccountProjection,
    UserCategoryProjection,
    UserExchangeRateProjection,
    UserTagProjection,
)
from .transaction_normalization import normalize_transaction_accounts

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


def _as_float_or_none(v: Any) -> float | None:
    """同 _as_float 但保留 NULL 语义(native_amount 缺失必须落 NULL,
    统计端靠 NULL 触发 COALESCE 回退 amount;默认 0.0 会把统计清零)。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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


def _parse_date(raw: Any):
    """定期存款/归物的日期列:吃 'YYYY-MM-DD' / ISO datetime / date / datetime。

    只取年月日(存款起息、到期、归还都是"日"粒度业务),存成当天 00:00 UTC,
    跟 happened_at 的 timezone-aware datetime 列型一致。
    """
    from datetime import date, datetime, timezone

    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        except ValueError:
            # 退化:截前 10 字符 'YYYY-MM-DD' 再解析一次
            try:
                d = date.fromisoformat(s[:10])
            except ValueError:
                return None
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return None


def _parse_datetime(raw: Any):
    """退款日期时间:与 _parse_date 同吃多种格式,但**保留时分秒**(不归零)。

    退款需要精确到分钟,所以这里不把时间抹成 00:00。date-only 入参时间补 00:00。
    """
    from datetime import date, datetime, timezone

    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            try:
                d = date.fromisoformat(s[:10])
                return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def _json_text_or_none(v: Any) -> str | None:
    """把 dict/list 形态的结构化 payload 归一成 JSON 文本列;空值归一成 None。

    折扣 / 手续费 / 合并付款 legs(0021)在 snapshot payload 里是 dict/list,
    在 projection 里是 Text。入参兼容已序列化的 str(移动端 merge 回填路径
    _json_loads_safe 反序列化后又交回来)。空 dict / 空 list → NULL(无优惠 /
    无手续费 / 单账户),不存 '{}' / '[]' 噪音。
    """
    if v is None:
        return None
    if isinstance(v, str):
        text = v.strip()
        if not text or text in {"{}", "[]", "null"}:
            return None
        return text
    if isinstance(v, (dict, list)):
        if not v:
            return None
        return json.dumps(v, ensure_ascii=False)
    return None


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

def _resolve_account_sync_id_by_name(
    db: Session, *, user_id: str, name: str | None
) -> str | None:
    """按 (user_id, name) 唯一反查 user_account_projection 的 sync_id。

    #41:老 web / 前端映射 miss 的交易只带账户名不带 id → 投影 account_sync_id
    为 NULL,被 sync_id 维度的统计/过滤漏算。恰好一个命中才补(0 个或同名多账户
    返回 None,宁缺勿错);与 mobile sync_engine_apply 的按名 fallback 同语义。
    """
    if not name:
        return None
    rows = db.scalars(
        select(UserAccountProjection.sync_id)
        .where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.name == name,
        )
        .limit(2)
    ).all()
    return rows[0] if len(rows) == 1 else None


def upsert_tx(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    payload = normalize_transaction_accounts(payload)
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

    # 共享账本:created_by_user_id 一旦写入就不能被后续 upsert 覆盖
    # (B 编辑 A 创建的 tx 时,payload 不带 createdByUserId,如果 _upsert
    # 把列设成 NULL,server 端 created_by 信息就丢了)。先查 existing row,
    # payload 没带 created → 保留 existing.created_by_user_id。
    existing_creator = db.scalar(
        select(ReadTxProjection.created_by_user_id).where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.sync_id == sync_id,
        )
    )
    payload_creator = _as_str(payload.get("createdByUserId"))

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
        # 创建人 first-write-wins:有 existing 就保留;首次插入用 payload 或回退
        # 到当前 actor(updatedByUserId)。
        "created_by_user_id": existing_creator
            or payload_creator
            or _as_str(payload.get("updatedByUserId")),
        # 共享账本 Phase 1:每次 upsert 都更新 last_edited_by_user_id;
        # payload 没带就回退 created。
        "last_edited_by_user_id": _as_str(payload.get("updatedByUserId")) or payload_creator,
        # 账单标记(.docs/transaction-flags)。缺键保留由上游 merge_with_existing
        # 负责(payload 已含既有行值),这里只做布尔强转;default=False 兜底首次插入。
        "exclude_from_stats": _as_bool(payload.get("excludeFromStats"), default=False),
        "exclude_from_budget": _as_bool(payload.get("excludeFromBudget"), default=False),
        # 交易级多币种(0018):缺键保留由上游 merge_with_existing 负责;首次
        # 插入且旧 payload 无字段 → NULL(统计端 COALESCE 回退 amount)。
        "currency_code": _as_str(payload.get("currencyCode")),
        "native_amount": _as_float_or_none(payload.get("nativeAmount")),
        # 优惠/折扣(0021):仅 expense 有意义的 dict;空 → NULL。
        # normalize_transaction_accounts 已按 tx_type 把跨类型字段清成 None。
        "discount_json": _json_text_or_none(payload.get("discount")),
        # 转账手续费(0021):仅 transfer 有意义的 dict;空 → NULL。
        "transfer_fee_json": _json_text_or_none(payload.get("transferFee")),
        # 多账户合并付款(0021):仅 expense 有意义的 leg 数组;空 → NULL。
        "payment_legs_json": _json_text_or_none(payload.get("paymentLegs")),
        # 成员分摊(0024):仅 expense 有意义的 share 数组;空 → NULL。
        # 按人拆分,**不影响账户余额**,只服务于成员维度统计。
        "member_shares_json": _json_text_or_none(payload.get("memberShares")),
        # 归物(0029):关联物品 sync_id;空 → NULL(不关联)。弱引用,
        # 物品被删不阻塞交易,只是从物品成本里消失。
        "item_sync_id": _as_str(payload.get("itemId")),
        # 定期存款利息(0030):关联交易 sync_id;空 → NULL(不关联)。弱引用,
        # 存款被删不阻塞交易,只是从「已收利息」聚合里消失。
        "time_deposit_sync_id": _as_str(payload.get("timeDepositId")),
        "source_change_id": source_change_id,
    }

    # #41:payload 只带名不带 id 时(老 web / 前端映射 miss),按名唯一反查补全。
    # 三组 account 字段各自独立处理;同名多账户保持 NULL,宁缺勿错。
    for id_key, name_key in (
        ("account_sync_id", "account_name"),
        ("from_account_sync_id", "from_account_name"),
        ("to_account_sync_id", "to_account_name"),
    ):
        if values[id_key] is None and values[name_key]:
            values[id_key] = _resolve_account_sync_id_by_name(
                db, user_id=user_id, name=values[name_key]
            )

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
        # 完整卡号 + 图标 key(0026)。nullable,老 snapshot 没这些 key 时落 None。
        "card_number": _as_str(payload.get("cardNumber")),
        "icon": _as_str(payload.get("icon")),
        # 别名(0027):展示用短名,老 snapshot 没这个 key 时落 None。
        "alias": _as_str(payload.get("alias")),
        # 出账日计入当期(0027)。NOT NULL 布尔:merge_with_existing_user 已把
        # 缺键的 billingDayInCurrent 从旧行补齐;全新 insert 首次缺失时给 True
        # (主流口径:账单日当天入本期)。
        "billing_day_in_current": _as_bool(
            payload.get("billingDayInCurrent"), default=True
        ),
        # 账户隐藏(issue #240)。merge_with_existing_user 已把缺键的 hidden
        # 从旧行补齐,这里直接取 merged payload 的值;全新 insert 首次缺失时给
        # False(未隐藏)。
        "hidden": _as_bool(payload.get("hidden"), default=False),
        "source_change_id": source_change_id,
    }
    # card_last_four 缺省时从完整卡号后四位派生(0026)。用户录了全卡号就不用
    # 再手填末四位;显式带 cardLastFour 时以用户为准(兼容 mobile 只传末四位)。
    if not values["card_last_four"] and values["card_number"]:
        digits = "".join(ch for ch in values["card_number"] if ch.isdigit())
        if len(digits) >= 4:
            values["card_last_four"] = digits[-4:]
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

    # 共享账本父子稳定关系:优先用 payload['parentSyncId'](mobile entity_serializer
    # / web snapshot_mutator 写入);若没有(老 client / 老数据),按
    # (user_id, parent_name, kind, level=1) 反查同 user 的 level=1 行兜底。
    parent_name = _as_str(payload.get("parentName"))
    parent_sync_id = _as_str(payload.get("parentSyncId"))
    if parent_sync_id is None and parent_name:
        parent_ids = db.scalars(
            select(UserCategoryProjection.sync_id).where(
                UserCategoryProjection.user_id == user_id,
                UserCategoryProjection.name == parent_name,
                UserCategoryProjection.kind == _as_str(payload.get("kind")),
                func.coalesce(UserCategoryProjection.level, 1) == 1,
                UserCategoryProjection.sync_id != sync_id,
            )
        ).all()
        parent_sync_id = parent_ids[0] if len(parent_ids) == 1 else None
    if parent_sync_id:
        parent = db.scalar(select(UserCategoryProjection).where(
            UserCategoryProjection.user_id == user_id,
            UserCategoryProjection.sync_id == parent_sync_id,
            UserCategoryProjection.kind == _as_str(payload.get("kind")),
            func.coalesce(UserCategoryProjection.level, 1) == 1,
        ))
        if parent is not None:
            parent_name = parent.name

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
        "parent_name": parent_name,
        "parent_sync_id": parent_sync_id,
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
# 定期存款 / 归物(0021)                                                        #
# --------------------------------------------------------------------------- #


def upsert_time_deposit(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """ledger-scope 定期存款 upsert。PK=(ledger_id, sync_id)。"""
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    start_date = _parse_date(payload.get("startDate"))
    maturity_date = _parse_date(payload.get("maturityDate"))
    if start_date is None or maturity_date is None:
        # 起息日/到期日是 NOT NULL 列;缺列无法表达一笔有效存款 → 拒绝写入。
        # 调用方(sync push / write)会因 500 / 事务回滚暴露,不会静默丢数据。
        logger.warning(
            "upsert_time_deposit: missing start/maturity date ledger=%s sync_id=%s",
            ledger_id, sync_id,
        )
        return
    status = (_as_str(payload.get("status")) or "holding").lower()
    if status not in {"holding", "matured", "withdrawn"}:
        status = "holding"
    interest_mode = (_as_str(payload.get("interestMode")) or "simple").lower()
    if interest_mode not in {"simple", "compound"}:
        interest_mode = "simple"
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "principal": _as_float(payload.get("principal")),
        "currency": (_as_str(payload.get("currency")) or "CNY")[:16],
        "annual_rate": _as_float(payload.get("annualRate")),
        "term_months": _as_int(payload.get("termMonths"), default=12) or 12,
        "start_date": start_date,
        "maturity_date": maturity_date,
        "status": status,
        "account_sync_id": _as_str(payload.get("accountId")),
        "account_name": _as_str(payload.get("accountName")),
        "auto_renew": _as_bool(payload.get("autoRenew"), default=False),
        "interest_mode": interest_mode,
        "note": _as_str(payload.get("note")),
        "withdrawn_at": _parse_date(payload.get("withdrawnAt")),
        "actual_interest": _as_float_or_none(payload.get("actualInterest")),
        "source_change_id": source_change_id,
    }
    # 到息计划(0028,多笔):只在 payload 显式带 payoutPlans 键时覆盖 —— 缺键
    # (update 未传)时不进 values,列保持现值(merge_with_existing 已补齐)。
    if "payoutPlans" in payload:
        plans = payload.get("payoutPlans")
        values["payout_plans_json"] = json.dumps(plans) if plans else None
    _upsert(db, ReadTimeDepositProjection, ("ledger_id", "sync_id"), values)


def delete_time_deposit(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadTimeDepositProjection, ledger_id=ledger_id, sync_id=sync_id)


def upsert_item_return(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """ledger-scope 归物 upsert。PK=(ledger_id, sync_id)。"""
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    happened_at = _parse_date(payload.get("happenedAt"))
    if happened_at is None:
        logger.warning(
            "upsert_item_return: missing happenedAt ledger=%s sync_id=%s",
            ledger_id, sync_id,
        )
        return
    status = (_as_str(payload.get("status")) or "pending").lower()
    if status not in {"pending", "returned", "refunded", "cancelled"}:
        status = "pending"
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "name": _as_str(payload.get("name")) or "",
        "original_tx_id": _as_str(payload.get("originalTxId")),
        "original_amount": _as_float_or_none(payload.get("originalAmount")),
        "refund_amount": _as_float(payload.get("refundAmount")),
        "happened_at": happened_at,
        "reason": _as_str(payload.get("reason")),
        "status": status,
        "account_sync_id": _as_str(payload.get("accountId")),
        "account_name": _as_str(payload.get("accountName")),
        "category_sync_id": _as_str(payload.get("categoryId")),
        "category_name": _as_str(payload.get("categoryName")),
        "note": _as_str(payload.get("note")),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadItemReturnProjection, ("ledger_id", "sync_id"), values)


def delete_item_return(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadItemReturnProjection, ledger_id=ledger_id, sync_id=sync_id)


def upsert_item(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """ledger-scope 归物(0029 物品持有)upsert。PK=(ledger_id, sync_id)。"""
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    purchase_date = _parse_date(payload.get("purchaseDate"))
    if purchase_date is None:
        # 购买日期是 NOT NULL 列(没它算不出持有天数/日均成本)→ 拒绝写入。
        logger.warning(
            "upsert_item: missing purchaseDate ledger=%s sync_id=%s",
            ledger_id, sync_id,
        )
        return
    status = (_as_str(payload.get("status")) or "in_use").lower()
    if status not in {"in_use", "disposed"}:
        status = "in_use"
    dtype_raw = _as_str(payload.get("disposalType"))
    disposal_type = dtype_raw.lower() if dtype_raw else None
    if disposal_type not in {"sold", "scrapped"}:
        disposal_type = None
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "name": _as_str(payload.get("name")) or "",
        "purchase_date": purchase_date,
        "purchase_amount": _as_float(payload.get("purchaseAmount")),
        "warranty_until": _parse_date(payload.get("warrantyUntil")),
        "status": status,
        "disposal_type": disposal_type,
        "disposal_date": _parse_date(payload.get("disposalDate")),
        # 只有二手卖出才认回收款;报废一律不记(validate_item 已归零)。
        "disposal_amount": (
            _as_float_or_none(payload.get("disposalAmount"))
            if disposal_type == "sold"
            else None
        ),
        "category_sync_id": _as_str(payload.get("categoryId")),
        "category_name": _as_str(payload.get("categoryName")),
        "account_sync_id": _as_str(payload.get("accountId")),
        "account_name": _as_str(payload.get("accountName")),
        "note": _as_str(payload.get("note")),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadItemProjection, ("ledger_id", "sync_id"), values)


def delete_item(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadItemProjection, ledger_id=ledger_id, sync_id=sync_id)


# --------------------------------------------------------------------------- #
# 交易退款(0022)                                                               #
# --------------------------------------------------------------------------- #


def upsert_refund(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """ledger-scope 交易退款 upsert。PK=(ledger_id, sync_id)。

    累计不超原单的校验在 web write 路径(routers/write/refunds.py 的
    _make_refund_validator)做 —— sync push 路径信任 mobile 本地已算好的数据,
    跟 payment_legs 的校验分层一致。
    """
    sync_id = _as_str(payload.get("syncId"))
    original_tx_id = _as_str(payload.get("originalTxId"))
    happened_at = _parse_datetime(payload.get("happenedAt"))
    if sync_id is None or original_tx_id is None or happened_at is None:
        # syncId / originalTxId / happenedAt 缺一就无法表达一笔有效退款
        # (original_tx_id / happened_at 是 NOT NULL 列)→ 拒绝写入,
        # 调用方会因 500 / 事务回滚暴露,不会静默丢数据。
        logger.warning(
            "upsert_refund: missing syncId/originalTxId/happenedAt ledger=%s sync_id=%s",
            ledger_id, sync_id,
        )
        return
    status = (_as_str(payload.get("status")) or "pending").lower()
    if status not in {"pending", "received", "cancelled"}:
        status = "pending"
    refund_mode = (_as_str(payload.get("refundMode")) or "unified").lower()
    if refund_mode not in {"unified", "split"}:
        refund_mode = "unified"
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "original_tx_id": original_tx_id,
        "original_amount": _as_float_or_none(payload.get("originalAmount")),
        "amount": _as_float(payload.get("amount")),
        "currency": (_as_str(payload.get("currency")) or "CNY")[:16],
        "account_sync_id": _as_str(payload.get("accountId")),
        "account_name": _as_str(payload.get("accountName")),
        "happened_at": happened_at,
        "reason": _as_str(payload.get("reason")),
        "status": status,
        "note": _as_str(payload.get("note")),
        # 退款分摊(0025):统一退/分别退。split 时 refund_legs_json 承载各腿,
        # 顶层 account_sync_id 可为空(unified 时腿为 NULL)。
        "refund_mode": refund_mode,
        "refund_legs_json": _json_text_or_none(payload.get("refundLegs")),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadRefundProjection, ("ledger_id", "sync_id"), values)


def delete_refund(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadRefundProjection, ledger_id=ledger_id, sync_id=sync_id)


# --------------------------------------------------------------------------- #
# 账本内自定义成员(0024)                                                      #
# --------------------------------------------------------------------------- #


def upsert_member(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """ledger-scope 自定义成员 upsert。PK=(ledger_id, sync_id)。

    名字必填(validate_member 已保证);sort_order 缺省 0,UI 按它 + sync_id 排序。
    """
    sync_id = _as_str(payload.get("syncId"))
    name = _as_str(payload.get("name"))
    if sync_id is None or not name:
        # 缺 syncId / name 无法表达一个有效成员(name 是 NOT NULL 列)→ 拒绝写入。
        logger.warning(
            "upsert_member: missing syncId/name ledger=%s sync_id=%s",
            ledger_id, sync_id,
        )
        return
    # 图标 / 关联记账人:**每次都显式写**(不适用的写 None)。
    # 投影是整行 upsert,漏键会保留上一版值 —— 清空时必须显式写 None,
    # 否则 UI 上"清除图标"永远清不掉(同 linked_transactions 账户字段的坑)。
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "name": name[:64],
        "note": _as_str(payload.get("note")),
        "icon": _as_str(payload.get("icon")),
        # web write 走 member_to_camel → linkedUserId;sync push 原生也是
        # camelCase。两种写法都认,避免哪条链路漏掉。
        "linked_user_id": _as_str(
            payload.get("linkedUserId") or payload.get("linked_user_id")
        ),
        "sort_order": _as_int(payload.get("sortOrder")),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadMemberProjection, ("ledger_id", "sync_id"), values)


def delete_member(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadMemberProjection, ledger_id=ledger_id, sync_id=sync_id)


# --------------------------------------------------------------------------- #
# 借款 / 报销(0023)                                                            #
# --------------------------------------------------------------------------- #


def upsert_loan(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """ledger-scope 借款 upsert。PK=(ledger_id, sync_id)。"""
    sync_id = _as_str(payload.get("syncId"))
    started_at = _parse_date(payload.get("startedAt"))
    if sync_id is None or started_at is None:
        logger.warning(
            "upsert_loan: missing syncId/startedAt ledger=%s sync_id=%s",
            ledger_id, sync_id,
        )
        return
    direction = (_as_str(payload.get("direction")) or "borrowed").lower()
    if direction not in {"borrowed", "lent"}:
        direction = "borrowed"
    status = (_as_str(payload.get("status")) or "active").lower()
    if status not in {"active", "repaid", "cancelled"}:
        status = "active"
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "direction": direction,
        "counterparty": _as_str(payload.get("counterparty")) or "",
        "principal": _as_float(payload.get("principal")),
        "currency": (_as_str(payload.get("currency")) or "CNY")[:16],
        "interest_rate": _as_float(payload.get("interestRate")),
        "started_at": started_at,
        "due_at": _parse_date(payload.get("dueAt")),
        "repaid_amount": _as_float(payload.get("repaidAmount")),
        "status": status,
        "account_sync_id": _as_str(payload.get("accountId")),
        "account_name": _as_str(payload.get("accountName")),
        "note": _as_str(payload.get("note")),
        "repayments_json": (
            json.dumps(payload["repayments"], ensure_ascii=False)
            if payload.get("repayments")
            else None
        ),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadLoanProjection, ("ledger_id", "sync_id"), values)


def delete_loan(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadLoanProjection, ledger_id=ledger_id, sync_id=sync_id)


def parse_loan_repayments(text: str | None) -> list[dict[str, Any]]:
    """read_loan_projection.repayments_json 文本 → 还款明细列表;容错返回 []。"""
    if not text:
        return []
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)]


def upsert_reimbursement(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """ledger-scope 报销 upsert。PK=(ledger_id, sync_id)。"""
    sync_id = _as_str(payload.get("syncId"))
    happened_at = _parse_date(payload.get("happenedAt"))
    if sync_id is None or happened_at is None:
        logger.warning(
            "upsert_reimbursement: missing syncId/happenedAt ledger=%s sync_id=%s",
            ledger_id, sync_id,
        )
        return
    status = (_as_str(payload.get("status")) or "pending").lower()
    if status not in {"pending", "reimbursed", "cancelled"}:
        status = "pending"
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "title": _as_str(payload.get("title")) or "",
        "payer": _as_str(payload.get("payer")),
        "reimburser": _as_str(payload.get("reimburser")),
        "amount": _as_float(payload.get("amount")),
        "currency": (_as_str(payload.get("currency")) or "CNY")[:16],
        "category_sync_id": _as_str(payload.get("categoryId")),
        "category_name": _as_str(payload.get("categoryName")),
        "happened_at": happened_at,
        "status": status,
        "account_sync_id": _as_str(payload.get("accountId")),
        "account_name": _as_str(payload.get("accountName")),
        "related_tx_id": _as_str(payload.get("relatedTxId")),
        "note": _as_str(payload.get("note")),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadReimbursementProjection, ("ledger_id", "sync_id"), values)


def delete_reimbursement(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(
        db, ReadReimbursementProjection, ledger_id=ledger_id, sync_id=sync_id
    )


def sum_refunds_for_tx(
    db: Session,
    *,
    ledger_id: str,
    original_tx_id: str,
    exclude_sync_id: str | None = None,
) -> float:
    """同一原单的「有效退款」合计(status != 'cancelled')。

    写入路径用它做累计不超原单校验(exclude_sync_id 排除正在编辑的那笔自身);
    读路径用它给每笔退款算 refunded_total / remaining_refundable。
    """
    stmt = (
        select(func.coalesce(func.sum(ReadRefundProjection.amount), 0.0))
        .where(
            ReadRefundProjection.ledger_id == ledger_id,
            ReadRefundProjection.original_tx_id == original_tx_id,
            ReadRefundProjection.status != "cancelled",
        )
    )
    if exclude_sync_id:
        stmt = stmt.where(ReadRefundProjection.sync_id != exclude_sync_id)
    return float(db.scalar(stmt) or 0.0)


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
    old_name: str | None = None,
    old_kind: str | None = None,
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
    parent = db.scalar(select(UserCategoryProjection).where(
        UserCategoryProjection.user_id == user_id,
        UserCategoryProjection.sync_id == category_sync_id,
    ))
    legacy_name = old_name or (parent.name if parent is not None else None)
    legacy_kind = old_kind or (parent.kind if parent is not None else new_kind)
    db.execute(update(UserCategoryProjection).where(
        UserCategoryProjection.user_id == user_id,
        UserCategoryProjection.sync_id != category_sync_id,
        or_(
            UserCategoryProjection.parent_sync_id == category_sync_id,
            and_(UserCategoryProjection.parent_sync_id.is_(None),
                 UserCategoryProjection.parent_name == legacy_name,
                 UserCategoryProjection.kind == legacy_kind),
        ),
    ).values(parent_name=new_name, parent_sync_id=category_sync_id))


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
    # 两条查询取并集:
    #   1) tag_sync_ids_json 精确引用了该 tag sync_id (mobile/web 完整数据)
    #   2) tags_csv 包含旧名称但没有 tag_sync_ids_json (legacy/不完整数据)
    like_pat = f'%"{tag_sync_id}"%'
    rows_by_id = db.scalars(
        sql_select(ReadTxProjection).where(
            ReadTxProjection.user_id == user_id,
            ReadTxProjection.tag_sync_ids_json.like(like_pat),
        )
    ).all()
    # tags_csv 可能是 "旧标签" 或 "A,旧标签,B" 等逗号分隔形式;
    # 用 LIKE 做粗筛,Python 侧按逗号拆分精确匹配防 substring 误伤。
    like_name = f"%{old_name}%"
    rows_by_name = db.scalars(
        sql_select(ReadTxProjection).where(
            ReadTxProjection.user_id == user_id,
            ReadTxProjection.tags_csv.like(like_name),
            ReadTxProjection.tag_sync_ids_json.is_(None),
        )
    ).all()
    # 去重 (理论上不会重叠,但防御性合并)
    seen: set[tuple[str, str]] = set()
    all_rows = []
    for row in (*rows_by_id, *rows_by_name):
        key = (row.ledger_id, row.sync_id)
        if key not in seen:
            seen.add(key)
            all_rows.append(row)
    for row in all_rows:
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


def _fileid_still_referenced_in_ledger(
    db: Session, *, ledger_id: str, file_id: str,
) -> bool:
    """某个 AttachmentFile 在该 ledger 下任何 user 的 tx projection 还有引用吗?

    跟 [_fileid_still_referenced] 不同之处:**按 ledger_id 过滤,不按 user_id**。

    共享账本场景下 tx 创建人(Editor)上传的 attachment 在 `attachment_files` 表里
    user_id = Editor,但 `read_tx_projection` 里行可能挂在 ledger owner 的 user_id 下。
    `_delete_tx` 的 GC 路径如果用 user_id 过滤就会漏 — 这里改按 ledger_id 扫,任何
    user 的 projection 行只要还引用这个 fileId,都算"被引用",不能 GC。

    category icon 不是 ledger-scope,继续走原 [_fileid_still_referenced]。
    """
    pat_no_space = f'%"cloudFileId":"{file_id}"%'
    pat_with_space = f'%"cloudFileId": "{file_id}"%'
    tx_hit = db.scalar(
        select(func.count())
        .select_from(ReadTxProjection)
        .where(
            ReadTxProjection.ledger_id == ledger_id,
            or_(
                ReadTxProjection.attachments_json.like(pat_no_space),
                ReadTxProjection.attachments_json.like(pat_with_space),
            ),
        )
    )
    return bool(tx_hit)


def gc_orphan_attachments_for_ledger(
    db: Session,
    *,
    ledger_id: str,
    file_ids: Iterable[str | None],
) -> int:
    """对给定 fileId 集合,若该 ledger 下任何 user 的 tx projection 都无引用 →
    删 AttachmentFile 行 + unlink 物理文件。返回实际清掉的条数。

    跟 [gc_orphan_attachments] 同款契约(调用前 projection 必须先删),区别是
    **scope 是 ledger 而不是 user**:

    - **引用检查**走 [_fileid_still_referenced_in_ledger](扫所有 user 在本 ledger
      的 projection,不限 user_id)
    - **DELETE 时按 ledger_id 过滤**,不按 user_id,所以 Editor 上传的附件(user_id
      = Editor)也能被 Owner-发起的 delete 路径正确清理。

    使用场景:**ledger-scope 实体的 delete handler**(tx)。
    category icon / 其它 user-scope 路径继续用 [gc_orphan_attachments]。
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

        if _fileid_still_referenced_in_ledger(
            db, ledger_id=ledger_id, file_id=file_id,
        ):
            continue

        att = db.scalar(
            select(AttachmentFile).where(
                AttachmentFile.id == file_id,
                AttachmentFile.ledger_id == ledger_id,
            )
        )
        if att is None:
            continue

        storage_path = att.storage_path
        db.delete(att)

        try:
            p = Path(storage_path)
            if p.exists():
                p.unlink()
        except OSError as exc:
            logger.warning(
                "attachment gc unlink failed ledger=%s file=%s path=%s err=%s",
                ledger_id, file_id, storage_path, exc,
            )

        cleaned += 1

    if cleaned:
        logger.info(
            "attachment gc ledger=%s cleaned=%d", ledger_id, cleaned,
        )
    return cleaned


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


# --------------------------------------------------------------------------- #
# 手动汇率 override:user_exchange_rate_projection                               #
# --------------------------------------------------------------------------- #

def upsert_exchange_rate_override(
    db: Session,
    *,
    user_id: str,
    source_change_id: int,
    payload: dict,
) -> None:
    """手动汇率 override → user_exchange_rate_projection。方向:1 quote = rate base。"""
    sync_id = str(payload.get("syncId") or "")
    base = str(payload.get("baseCurrency") or "").upper()
    quote = str(payload.get("quoteCurrency") or "").upper()
    rate = str(payload.get("rate") or "")
    if not sync_id or not base or not quote or not rate:
        return
    updated_at = _parse_happened_at(payload.get("updatedAt"))
    values = {
        "user_id": user_id,
        "sync_id": sync_id,
        "base_currency": base,
        "quote_currency": quote,
        "rate": rate,
        "updated_at": updated_at,
        "source_change_id": source_change_id,
    }
    _upsert(db, UserExchangeRateProjection, ("user_id", "sync_id"), values)


def delete_exchange_rate_override(db: Session, *, user_id: str, sync_id: str) -> None:
    """user-global exchange_rate_override delete。PK=(user_id, sync_id)。"""
    db.execute(
        delete(UserExchangeRateProjection).where(
            UserExchangeRateProjection.user_id == user_id,
            UserExchangeRateProjection.sync_id == sync_id,
        )
    )
