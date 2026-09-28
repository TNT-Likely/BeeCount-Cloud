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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .models import (
    AttachmentFile,
    Ledger,
    ReadBudgetProjection,
    ReadCardRewardRuleProjection,
    ReadDebtProjection,
    ReadStockTradeProjection,
    ReadInstallmentPeriodProjection,
    ReadInstallmentPlanProjection,
    ReadProjectCategoryBudgetProjection,
    ReadProjectProjection,
    ReadRecurringRuleProjection,
    ReadTxProjection,
    ReadTxSplitProjection,
    ReadTxTemplateProjection,
    UserAccountProjection,
    UserCategoryProjection,
    UserExchangeRateProjection,
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


def _as_int_or_none(v: Any) -> int | None:
    """同 _as_int 但保留 NULL 语义(settlement_days 缺失必须落 NULL,不能
    被默认值 0 误当成"0 天后入帳")。"""
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


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


def _parse_date_only(raw: Any):
    """periodStart/periodEnd(專案 Phase 13)是純日期(Date 欄位,無時區),
    跟 happenedAt 的 DateTime 不同,不能複用 _parse_happened_at。"""
    from datetime import date, datetime

    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- #
# Dialect 中立的 upsert                                                         #
# --------------------------------------------------------------------------- #
# SQLite / PostgreSQL 都用 INSERT ... ON CONFLICT DO UPDATE,但两边的方言专属
# insert()/OnConflictDoUpdate 子句类互不相通,必须按 bind 方言各自用
# `dialects.sqlite.insert` / `dialects.postgresql.insert` 构造,不能混用同一个
# 方言的 insert() 去喂给另一个方言的 compiler。未知方言 fallback 到先 SELECT
# 再 UPDATE/INSERT。

def _dialect_name(bind) -> str:
    try:
        return bind.dialect.name if hasattr(bind, "dialect") else bind.bind.dialect.name
    except AttributeError:
        return "sqlite"


def _upsert(db: Session, model, pk_fields: tuple[str, ...], values: dict) -> None:
    """通用 upsert:主键撞了就 UPDATE 其他所有列。"""
    bind = db.get_bind()
    dialect_name = _dialect_name(bind)
    if dialect_name in ("sqlite", "postgresql"):
        # SQLite / PG 都支持 ON CONFLICT,但两边的 Insert/OnConflict 子句是不同
        # 的类(字段也不同),SQLAlchemy 不会跨方言互相翻译 —— 必须按实际连线
        # 的方言选对应的 insert() 构造函数,否则 PG 编译器处理 sqlite 方言产生
        # 的子句会因缺少 `constraint_target` 等属性直接 AttributeError。
        insert_fn = pg_insert if dialect_name == "postgresql" else sqlite_insert
        stmt = insert_fn(model).values(**values)
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


def _normalize_tx_splits_for_projection(raw: Any) -> list[dict[str, Any]]:
    """`payload["splits"]`(item 里已经是 camelCase dict 列表,由
    snapshot_mutator._normalize_tx_splits 规范化过)→ 落 read_tx_split_projection
    的 dict 列表,过滤掉没有 categoryId 的脏行。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        category_sync_id = _as_str(entry.get("categoryId"))
        if category_sync_id is None:
            continue
        out.append({
            "sort_order": _as_int(entry.get("sortOrder"), default=idx),
            "category_sync_id": category_sync_id,
            "category_name": _as_str(entry.get("categoryName")),
            "amount": _as_float(entry.get("amount")),
            "note": _as_str(entry.get("note")),
        })
    return out


def _replace_tx_splits(
    db: Session, *, ledger_id: str, tx_sync_id: str, user_id: str, splits_raw: Any,
) -> bool:
    """拆帳(§2.4):整批 delete-then-insert `read_tx_split_projection` 里
    (ledger_id, tx_sync_id) 下的所有行,权威值是 payload["splits"](已经过
    `merge_with_existing`/`_projection_row_to_tx_dict` 补齐"没传就沿用旧值"
    的语义,到这里时永远是"这次 upsert 后应该有的最终列表")。返回是否有
    splits(供调用方设置 has_splits 列)。"""
    db.execute(
        delete(ReadTxSplitProjection).where(
            ReadTxSplitProjection.ledger_id == ledger_id,
            ReadTxSplitProjection.tx_sync_id == tx_sync_id,
        )
    )
    rows = _normalize_tx_splits_for_projection(splits_raw)
    for row in rows:
        db.add(ReadTxSplitProjection(
            ledger_id=ledger_id,
            tx_sync_id=tx_sync_id,
            user_id=user_id,
            **row,
        ))
    return bool(rows)


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

    reward_rule_ids = payload.get("rewardRuleIds")
    reward_rule_sync_ids_json = (
        json.dumps(reward_rule_ids) if isinstance(reward_rule_ids, list) else None
    )

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
        "merchant": _as_str(payload.get("merchant")),
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
        # 跨幣別轉帳(2026-08):缺键保留由上游 merge_with_existing 负责;首次
        # 插入且旧 payload 无字段 → NULL(讀取端 COALESCE(to_amount, amount)
        # 回退,同幣種轉帳的既有行為)。
        "to_amount": _as_float_or_none(payload.get("toAmount")),
        # 手續費/折扣(2026-08 使用者需求):base_amount 缺失必须落 NULL(=
        # 從未使用過這個功能),用 _as_float_or_none 保留 NULL 语义,同
        # native_amount 写法。
        "base_amount": _as_float_or_none(payload.get("baseAmount")),
        "fee_amount": _as_float_or_none(payload.get("feeAmount")),
        "fee_label": _as_str(payload.get("feeLabel")),
        "discount_amount": _as_float_or_none(payload.get("discountAmount")),
        "discount_label": _as_str(payload.get("discountLabel")),
        # 退款(§2.6)/ 分期(§2.3)反查字段:None = 普通交易,缺键保留由上游
        # merge_with_existing 负责。
        "refund_of_sync_id": _as_str(payload.get("refundOfId")),
        "installment_plan_sync_id": _as_str(payload.get("installmentPlanId")),
        # 週期性收支(§2.12.2 Phase 1.5)反查字段 + 单笔编辑标记,同款语义。
        "recurring_rule_sync_id": _as_str(payload.get("recurringRuleId")),
        "recurring_occurrence_overridden": _as_bool(
            payload.get("recurringOccurrenceOverridden"), default=False
        ),
        # 借還款追蹤(§2.5 Phase 3)反查字段,同款语义:None = 普通交易,缺键
        # 保留由上游 merge_with_existing 负责。
        "debt_sync_id": _as_str(payload.get("debtId")),
        # 專案(Phase 13)反查字段,同款语义:None = 沒掛專案,缺键保留由
        # 上游 merge_with_existing 负责。
        "project_sync_id": _as_str(payload.get("projectId")),
        # 信用卡紅利回饋(§2.9.5,2026-08-06 改版)反查字段,同款语义:None =
        # 没勾选任何规则,缺键保留由上游 merge_with_existing 负责。
        "reward_rule_sync_ids_json": reward_rule_sync_ids_json,
        # 信用卡紅利回饋自動入帳(§2.9.5.4 補強)反查字段,同款语义:None =
        # 普通交易(或 period_end/manual 這種不對應單一原始交易的回饋)。
        "reward_source_tx_sync_id": _as_str(payload.get("rewardSourceTxId")),
        # 延後入帳(§2.10 Phase 5):None = 正常交易,缺键保留由上游
        # merge_with_existing 负责。
        "deferred_posting_at": (
            _parse_happened_at(payload.get("deferredPostingAt"))
            if payload.get("deferredPostingAt") else None
        ),
        # 對帳模式(§2.10,2026-08-09 改版):None = 尚未核對,缺键保留由上游
        # merge_with_existing 负责。
        "reconciled_at": (
            _parse_happened_at(payload.get("reconciledAt"))
            if payload.get("reconciledAt") else None
        ),
        "source_change_id": source_change_id,
    }

    # 拆帳(§2.4):has_splits 时父行的 category_sync_id/category_name 强制清空
    # (前端靠这两个字段是否为 None 判断是否显示"多分类"),避免拆帳交易的
    # 父行 category 跟 splits 明细里的分类混淆展示。
    splits_raw = payload.get("splits")
    has_splits = isinstance(splits_raw, list) and len(splits_raw) > 0
    values["has_splits"] = has_splits
    values["splits_json"] = json.dumps(splits_raw) if has_splits else None
    if has_splits:
        values["category_sync_id"] = None
        values["category_name"] = None

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
    _replace_tx_splits(
        db, ledger_id=ledger_id, tx_sync_id=sync_id, user_id=user_id,
        splits_raw=splits_raw,
    )

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

    # 取 prev avatar_cloud_file_id —— upsert 后跟 new 比对,若变更则 GC 旧
    # attachment,跟 upsert_category 的 icon_cloud_file_id 同款套路,防止
    # 用户换头像/移除头像后旧 cloud blob 永远成孤儿。
    prev_avatar_file_id: str | None = None
    prev_avatar_row = db.scalar(
        select(UserAccountProjection.avatar_cloud_file_id).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.sync_id == sync_id,
        )
    )
    if prev_avatar_row:
        prev_avatar_file_id = prev_avatar_row.strip() if isinstance(prev_avatar_row, str) else None

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
        # 主帳戶(合併帳單,§2.9 Phase 4):子卡指向主卡的 sync_id。
        "parent_account_id": _as_str(payload.get("parentAccountId")),
        # 自動扣繳(§2.9,2026-08-04 改版):開關 + 來源帳戶 sync_id。
        "auto_pay_enabled": _as_bool(payload.get("autoPayEnabled"), default=False),
        "auto_pay_from_account_id": _as_str(payload.get("autoPayFromAccountId")),
        # 账户隐藏(issue #240)。merge_with_existing_user 已把缺键的 hidden
        # 从旧行补齐,这里直接取 merged payload 的值;全新 insert 首次缺失时给
        # False(未隐藏)。
        "hidden": _as_bool(payload.get("hidden"), default=False),
        # 帳戶頭像(2026-08-02 補強)。
        "avatar_cloud_file_id": _as_str(payload.get("avatarCloudFileId")),
        "avatar_cloud_sha256": _as_str(payload.get("avatarCloudSha256")),
        # SwipeSmart 卡片對照(Phase 14)。
        "swipesmart_card_id": _as_str(payload.get("swipesmartCardId")),
        # 納入總餘額(Phase 18)。merge_with_existing_user 已把缺鍵的
        # includeInTotal 從舊行補齊,這裡直接取 merged payload 的值;全新
        # insert 首次缺失時給 True(預設納入)。
        "include_in_total": _as_bool(payload.get("includeInTotal"), default=True),
        # 帳戶清單拖曳排序(2026-09-05)。
        "sort_order": _opt_int(payload.get("sortOrder")),
        # 股票持股(2026-09-28):wire 上是物件,落 TEXT。merge_with_existing_user
        # 已用 _json_loads_safe 把缺鍵的舊值補成 dict,這裡直接 dumps;非 dict
        # (舊 App 不帶/顯式 null)落 None。
        "investment_settings_json": (
            json.dumps(payload.get("investmentSettings"), ensure_ascii=False)
            if isinstance(payload.get("investmentSettings"), dict)
            else None
        ),
        "source_change_id": source_change_id,
    }
    _upsert(db, UserAccountProjection, ("user_id", "sync_id"), values)

    # 如果 prev_avatar_file_id 跟新值不一样(包括清空、换图),且 prev 不是空,
    # 把旧 fileId 交给 gc_orphan_attachments —— 真孤儿才删 AttachmentFile +
    # 物理文件,被共用的会留着。
    new_avatar_file_id = _as_str(payload.get("avatarCloudFileId")) or None
    if prev_avatar_file_id and prev_avatar_file_id != new_avatar_file_id:
        gc_orphan_attachments(db, user_id=user_id, file_ids=[prev_avatar_file_id])


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
        parent_sync_id = db.scalar(
            select(UserCategoryProjection.sync_id).where(
                UserCategoryProjection.user_id == user_id,
                UserCategoryProjection.name == parent_name,
                UserCategoryProjection.kind == _as_str(payload.get("kind")),
                func.coalesce(UserCategoryProjection.level, 1) == 1,
            )
        )

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
        "color": _as_str(payload.get("color")),
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


def upsert_recurring_rule(
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
        "tx_type": _as_str(payload.get("txType")) or "expense",
        "amount": _as_float(payload.get("amount")),
        "note": _as_str(payload.get("note")),
        "category_sync_id": _as_str(payload.get("categoryId")),
        "account_sync_id": _as_str(payload.get("accountId")),
        "from_account_sync_id": _as_str(payload.get("fromAccountId")),
        "to_account_sync_id": _as_str(payload.get("toAccountId")),
        "merchant": _as_str(payload.get("merchant")),
        "project_sync_id": _as_str(payload.get("projectId")),
        "tag_sync_ids_json": (
            json.dumps(payload.get("tagIds"))
            if isinstance(payload.get("tagIds"), list) else None
        ),
        "frequency": _as_str(payload.get("frequency")) or "monthly",
        "interval": _as_int(payload.get("interval"), default=1) or 1,
        "next_run_at": _parse_happened_at(payload.get("nextRunAt")),
        "end_at": _parse_happened_at(payload.get("endAt")) if payload.get("endAt") else None,
        "enabled": _as_bool(payload.get("enabled"), default=True),
        # Phase 1.5(§2.12.2):视窗续产生进度 + 进阶规则,None = 未设置。
        # advancedRuleJson 在 snapshot 层是嵌套 dict(跟 attachments/tagIds
        # 同惯例),这里落库前才 json.dumps 成 TEXT 列。
        "generated_until_at": (
            _parse_happened_at(payload.get("generatedUntilAt"))
            if payload.get("generatedUntilAt") else None
        ),
        "advanced_rule_json": (
            json.dumps(payload.get("advancedRuleJson"))
            if isinstance(payload.get("advancedRuleJson"), (dict, list))
            else _as_str(payload.get("advancedRuleJson"))
        ),
        # 手續費/折扣/信用卡回饋(2026-08 使用者回饋):同 upsert_tx 同名欄位
        # 寫法,base_amount 缺失落 NULL(= 從未使用過這個功能)。
        "base_amount": _as_float_or_none(payload.get("baseAmount")),
        "fee_amount": _as_float_or_none(payload.get("feeAmount")),
        "fee_label": _as_str(payload.get("feeLabel")),
        "discount_amount": _as_float_or_none(payload.get("discountAmount")),
        "discount_label": _as_str(payload.get("discountLabel")),
        "reward_rule_sync_ids_json": (
            json.dumps(payload.get("rewardRuleIds"))
            if isinstance(payload.get("rewardRuleIds"), list) else None
        ),
        # 股票定期定額(2026-09-28):kind 缺省時落 'general'(既有規則沒有這個
        # 欄位),market/symbol/security_name/stock_fee_rate/stock_fee_min 只有
        # kind='stock_dca' 才有值。
        "kind": _as_str(payload.get("kind")) or "general",
        "market": (_as_str(payload.get("market")) or "").upper() or None,
        "symbol": (_as_str(payload.get("symbol")) or "").upper() or None,
        "security_name": _as_str(payload.get("securityName")),
        "stock_fee_rate": _as_float_or_none(payload.get("stockFeeRate")),
        "stock_fee_min": _as_float_or_none(payload.get("stockFeeMin")),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadRecurringRuleProjection, ("ledger_id", "sync_id"), values)


def delete_recurring_rule(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadRecurringRuleProjection, ledger_id=ledger_id, sync_id=sync_id)


def upsert_installment_plan(
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
        "total_amount": _as_float(payload.get("totalAmount")),
        "periods": _as_int(payload.get("periods"), default=1) or 1,
        "period_amount": _as_float(payload.get("periodAmount")),
        "first_period_at": _parse_happened_at(payload.get("firstPeriodAt")),
        "next_period_at": _parse_happened_at(payload.get("nextPeriodAt")),
        "paid_periods": _as_int(payload.get("paidPeriods"), default=0),
        "account_sync_id": _as_str(payload.get("accountId")),
        "category_sync_id": _as_str(payload.get("categoryId")),
        "note": _as_str(payload.get("note")),
        "status": _as_str(payload.get("status")) or "active",
        # Phase 1.5(§2.12.1)攤還算法参数,default 对齐既有 Phase 1 行为
        # (等额本金/无息)。
        "repayment_method": _as_str(payload.get("repaymentMethod")) or "equal_principal",
        "interest_period": _as_str(payload.get("interestPeriod")) or "monthly",
        "interest_rate": _as_float(payload.get("interestRate")),
        "round_amounts": _as_bool(payload.get("roundAmounts"), default=True),
        "remainder_position": _as_str(payload.get("remainderPosition")) or "last",
        "grace_period_months": _as_int(payload.get("gracePeriodMonths"), default=0),
        "offset_breakdown_json": _as_str(payload.get("offsetBreakdownJson")),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadInstallmentPlanProjection, ("ledger_id", "sync_id"), values)


def delete_installment_plan(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadInstallmentPlanProjection, ledger_id=ledger_id, sync_id=sync_id)


def upsert_debt(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """借還款追蹤(§2.5 Phase 3)。remaining_amount/status 不落库,见
    ReadDebtProjection docstring —— 这里只落 principal_amount 等静态字段。"""
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "direction": _as_str(payload.get("direction")) or "payable",
        "counterparty_name": _as_str(payload.get("counterpartyName")) or "",
        "principal_amount": _as_float(payload.get("principalAmount")),
        "due_at": _parse_happened_at(payload.get("dueAt")) if payload.get("dueAt") else None,
        "note": _as_str(payload.get("note")),
        "closed_at": _parse_happened_at(payload.get("closedAt")) if payload.get("closedAt") else None,
        "category_sync_id": _as_str(payload.get("categoryId")),
        "origin_tx_sync_id": _as_str(payload.get("originTxId")),
        "excluded_from_total": _as_bool(payload.get("excludedFromTotal"), default=False),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadDebtProjection, ("ledger_id", "sync_id"), values)


def delete_debt(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadDebtProjection, ledger_id=ledger_id, sync_id=sync_id)


def upsert_stock_trade(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """股票交易明細(2026-09-28,docs/STOCK_HOLDINGS_SD.md)。持股不落庫,見
    ReadStockTradeProjection docstring。`_upsert` 是整列覆蓋,呼叫方
    (sync_applier merge / snapshot diff-emit)必須帶完整欄位。"""
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "account_sync_id": _as_str(payload.get("accountId")),
        "market": (_as_str(payload.get("market")) or "").upper(),
        "symbol": (_as_str(payload.get("symbol")) or "").upper(),
        "security_name": _as_str(payload.get("securityName")),
        "trade_type": _as_str(payload.get("tradeType")) or "buy",
        "shares": _as_float(payload.get("shares")),
        "price": _as_float_or_none(payload.get("price")),
        "fee": _as_float(payload.get("fee")),
        "tax": _as_float(payload.get("tax")),
        "amount": _as_float(payload.get("amount")),
        "currency": (_as_str(payload.get("currency")) or "").upper() or None,
        "trade_date": _parse_happened_at(payload.get("tradeDate")),
        "tx_sync_id": _as_str(payload.get("txId")),
        "dividend_event_ref": _as_str(payload.get("dividendEventRef")),
        "note": _as_str(payload.get("note")),
        "created_by_user_id": _as_str(payload.get("createdByUserId")),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadStockTradeProjection, ("ledger_id", "sync_id"), values)


def delete_stock_trade(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadStockTradeProjection, ledger_id=ledger_id, sync_id=sync_id)


def upsert_project(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """專案(Phase 13,docs/PH13_PROJECT_SD.md)。花費彙總不落庫,見
    ReadProjectProjection docstring —— 这里只落名稱/預算/期間等靜態字段。"""
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "name": _as_str(payload.get("name")) or "",
        "icon": _as_str(payload.get("icon")),
        "budget_amount": _as_float_or_none(payload.get("budgetAmount")),
        "period_type": _as_str(payload.get("periodType")) or "monthly",
        "period_start": _parse_date_only(payload.get("periodStart")),
        "period_end": _parse_date_only(payload.get("periodEnd")),
        "carryover_enabled": _as_bool(payload.get("carryoverEnabled"), default=False),
        "visible_on_home": _as_bool(payload.get("visibleOnHome"), default=True),
        "enabled": _as_bool(payload.get("enabled"), default=True),
        "sort_order": _as_int(payload.get("sortOrder"), default=0),
        "income_included_in_budget": _as_bool(payload.get("incomeIncludedInBudget"), default=False),
        "daily_budget_enabled": _as_bool(payload.get("dailyBudgetEnabled"), default=False),
        "daily_budget_mode": _as_str(payload.get("dailyBudgetMode")),
        "reminder_threshold_percent": _as_int_or_none(payload.get("reminderThresholdPercent")),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadProjectProjection, ("ledger_id", "sync_id"), values)


def delete_project(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadProjectProjection, ledger_id=ledger_id, sync_id=sync_id)


def upsert_project_category_budget(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """專案分類子預算(docs/2026-09-06-project-category-budget-period-switch-
    design.md §2.2/§7.2)。`projectId`/`categoryId` 跟其它同步實體的外鍵一樣
    只存 sync_id 字串,不做存在性驗證(這個 repo 沒有「sync_id 轉內部 id」的
    機制,存在性 + 一級分類校驗放在 write router 層)。"""
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "project_sync_id": _as_str(payload.get("projectId")),
        "category_sync_id": _as_str(payload.get("categoryId")),
        "mode": _as_str(payload.get("mode")) or "fixed",
        "fixed_amount": _as_float_or_none(payload.get("fixedAmount")),
        "percentage": _as_float_or_none(payload.get("percentage")),
        "carryover_enabled": _as_bool(payload.get("carryoverEnabled"), default=False),
        "sort_order": _as_int(payload.get("sortOrder"), default=0),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadProjectCategoryBudgetProjection, ("ledger_id", "sync_id"), values)


def delete_project_category_budget(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadProjectCategoryBudgetProjection, ledger_id=ledger_id, sync_id=sync_id)


def upsert_tx_template(
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
    tag_ids = payload.get("tagIds")
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "name": _as_str(payload.get("name")) or "",
        "tx_type": _as_str(payload.get("txType")) or "expense",
        "amount": _as_float(payload.get("amount")),
        "note": _as_str(payload.get("note")),
        "category_sync_id": _as_str(payload.get("categoryId")),
        "account_sync_id": _as_str(payload.get("accountId")),
        "from_account_sync_id": _as_str(payload.get("fromAccountId")),
        "to_account_sync_id": _as_str(payload.get("toAccountId")),
        "tag_sync_ids_json": json.dumps(tag_ids) if isinstance(tag_ids, list) else None,
        "sort_order": _as_int(payload.get("sortOrder"), default=0),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadTxTemplateProjection, ("ledger_id", "sync_id"), values)


def delete_tx_template(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadTxTemplateProjection, ledger_id=ledger_id, sync_id=sync_id)


def upsert_installment_period(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """§2.12.1 Phase 1.5:分期每期明细。只由 server 端写入口(建计画/rebalance/
    早偿/提前结清等)生成,client 不会直接建立单笔 period,但仍走完整
    sync entity 六步(跨装置需要可见)。"""
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    values = {
        "ledger_id": ledger_id,
        "sync_id": sync_id,
        "user_id": user_id,
        "plan_sync_id": _as_str(payload.get("planId")) or "",
        "period_no": _as_int(payload.get("periodNo"), default=1) or 1,
        "due_at": _parse_happened_at(payload.get("dueAt")),
        "principal_amount": _as_float(payload.get("principalAmount")),
        "interest_amount": _as_float(payload.get("interestAmount")),
        "total_amount": _as_float(payload.get("totalAmount")),
        "status": _as_str(payload.get("status")) or "generated",
        "tx_sync_id": _as_str(payload.get("txId")),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadInstallmentPeriodProjection, ("ledger_id", "sync_id"), values)


def delete_installment_period(db: Session, *, ledger_id: str, sync_id: str) -> None:
    delete_entity(db, ReadInstallmentPeriodProjection, ledger_id=ledger_id, sync_id=sync_id)


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
    # 拆帳(§2.4):tx_sync_id 不是外键,ledger 级联删不到这张表,删单笔交易
    # 要顺手清掉它的 split 明细行,否则留下指向已删除交易的孤儿行。
    db.execute(
        delete(ReadTxSplitProjection).where(
            ReadTxSplitProjection.ledger_id == ledger_id,
            ReadTxSplitProjection.tx_sync_id == sync_id,
        )
    )


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


def upsert_card_reward_rule(
    db: Session,
    *,
    user_id: str,
    source_change_id: int,
    payload: dict[str, Any],
) -> None:
    """信用卡紅利回饋規則(§2.9.5 Phase 4.5)。user-global,PK=(user_id, sync_id)
    ——見 ReadCardRewardRuleProjection docstring。回饋金額不落庫,這裡只寫
    規則本身的靜態設定欄位。"""
    sync_id = _as_str(payload.get("syncId"))
    if sync_id is None:
        return
    category_ids = payload.get("categoryIds")
    category_ids_json = (
        json.dumps(category_ids) if isinstance(category_ids, list) and category_ids else None
    )
    values = {
        "user_id": user_id,
        "sync_id": sync_id,
        "account_sync_id": _as_str(payload.get("accountId")) or "",
        "label": _as_str(payload.get("label")) or "",
        "category_sync_ids_json": category_ids_json,
        "rate_type": _as_str(payload.get("rateType")) or "percentage",
        "rate_value": _as_float(payload.get("rateValue")),
        "rounding": _as_str(payload.get("rounding")) or "round",
        "total_rounding": _as_str(payload.get("totalRounding")) or "round",
        "calc_basis": _as_str(payload.get("calcBasis")) or "transaction_date",
        "interval": _as_str(payload.get("interval")) or "billing_cycle",
        "min_spend_threshold": _as_float_or_none(payload.get("minSpendThreshold")),
        "min_tx_amount": _as_float_or_none(payload.get("minTxAmount")),
        "cap_amount": _as_float_or_none(payload.get("capAmount")),
        "cap_shared_key": _as_str(payload.get("capSharedKey")),
        "starts_at": _parse_happened_at(payload.get("startsAt")) if payload.get("startsAt") else None,
        "ends_at": _parse_happened_at(payload.get("endsAt")) if payload.get("endsAt") else None,
        "settlement_type": _as_str(payload.get("settlementType")) or "manual",
        "settlement_days": _as_int_or_none(payload.get("settlementDays")),
        "settlement_month_offset": _as_int_or_none(payload.get("settlementMonthOffset")),
        "settlement_day_of_month": _as_int_or_none(payload.get("settlementDayOfMonth")),
        "reward_account_id": _as_str(payload.get("rewardAccountId")),
        "note": _as_str(payload.get("note")),
        "enabled": _as_bool(payload.get("enabled"), default=True),
        "source_change_id": source_change_id,
    }
    _upsert(db, ReadCardRewardRuleProjection, ("user_id", "sync_id"), values)


def delete_card_reward_rule(db: Session, *, user_id: str, sync_id: str) -> None:
    """user-global card_reward_rule delete。PK=(user_id, sync_id)。"""
    db.execute(
        delete(ReadCardRewardRuleProjection).where(
            ReadCardRewardRuleProjection.user_id == user_id,
            ReadCardRewardRuleProjection.sync_id == sync_id,
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
    for model in (
        ReadTxProjection,
        ReadBudgetProjection,
        ReadRecurringRuleProjection,
        ReadInstallmentPlanProjection,
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
    for item in snapshot.get("recurringRules") or []:
        if isinstance(item, dict):
            upsert_recurring_rule(
                db,
                ledger_id=ledger_id,
                user_id=user_id,
                source_change_id=source_change_id,
                payload=item,
            )
    for item in snapshot.get("installmentPlans") or []:
        if isinstance(item, dict):
            upsert_installment_plan(
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
#   - user_account_projection.avatar_cloud_file_id  (单列,2026-08-02 補強)
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
    if cat_hit:
        return True

    # 帳戶頭像(2026-08-02 補強):跟 category icon 同款 user-scope 单列引用。
    acc_hit = db.scalar(
        select(func.count())
        .select_from(UserAccountProjection)
        .where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.avatar_cloud_file_id == file_id,
        )
    )
    return bool(acc_hit)


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
