"""Sync change → projection 应用层。

这个模块负责"把一条 SyncChange 怎么落到 read_*_projection 表里"的全部业务
逻辑。从 HTTP 层(``src.routers.sync`` 的 /sync/push 端点)分出来,目的:

1. **关注点分离**:HTTP 层只管路由 / auth / LWW / 事务提交,业务逻辑在这里。
   push_changes 的批量循环里一行 ``apply_change_to_projection(...)`` 即可。
2. **review 粒度**:未来修 projection 写入的逻辑(rename cascade / 字段合并
   / 图标兜底 / 附件 GC),改动集中在本文件,reviewer 不用翻 router 找线索。
3. **复现 + 测试**:业务逻辑脱离 FastAPI 之后,单元测试可以直接构造
   ``SyncChange`` 对象 + 手造 session 跑,不用再过 TestClient。

## 架构概览(user-global 重构后)

Push 路径跟 projection 的交互按 scope 分两条:

    /sync/push (router)
        ├─ scope='ledger' → apply_change_to_projection(change)      → projection.upsert_tx/budget
        └─ scope='user'   → apply_user_change_to_projection(change) → projection.upsert_account/category/tag

每条路径都有自己的三张"表"驱动 dispatch:

- ledger-scope:``_LEDGER_MERGE_SPECS`` / ``_LEDGER_UPSERT_DISPATCH`` / ``_LEDGER_DELETE_DISPATCH``
- user-scope: ``_USER_MERGE_SPECS`` / ``_USER_UPSERT_DISPATCH`` / ``_USER_DELETE_DISPATCH``

新加 entity 只登记其中两张会在测试 / assert 时爆出 KeyError(2026-04 踩过
的 copy-paste bug 在表驱动后已复现不了)。

## Rename cascade 的位置

account / category / tag 是 user-global 实体,name 变了之后 ReadTxProjection
里的冗余列(account_name / category_name / tags_csv)要一起刷。detect 写在
``apply_user_change_to_projection`` 里(不在 merge 里),因为它必须在 upsert
当前实体 *之前* 跑 —— cascade 用的是 SQL 单条 UPDATE 匹配**旧名**,upsert
之后旧名就丢了。范围是该用户的所有 ledger(单条 SQL WHERE user_id=X,不再
循环 ledger)。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.orm import Session

from . import projection
from .models import (
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
    ReadTxTemplateProjection,
    SyncChange,
    UserAccountProjection,
    UserCategoryProjection,
    UserExchangeRateProjection,
    UserTagProjection,
)
from .services.category_icon import resolve_icon_by_name


# 哪些 entity_type 可以走单条 change 的 projection 应用(其它 entity
# 比如 ``ledger_snapshot`` 是 sync_changes 里的元数据行,不走这条路径)。
INDIVIDUAL_ENTITY_TYPES = {
    "transaction", "account", "category", "tag", "budget", "ledger",
    "exchange_rate_override", "recurring_rule", "installment_plan",
    "installment_period", "debt", "tx_template", "card_reward_rule",
    "project", "project_category_budget", "stock_trade",
}

# user-global entity 类型白名单 —— 跟 mobile lib/cloud/sync/change_tracker.dart
# 的 userGlobalEntityTypes 保持一致。push 路径按这个集合分流到 user-scope 应用。
# card_reward_rule(§2.9.5 Phase 4.5):绑定的信用卡账户本身是 user-global,
# 规则跟着走同一个 scope,不挂任何 ledger。
USER_GLOBAL_ENTITY_TYPES = {"account", "category", "tag", "exchange_rate_override", "card_reward_rule"}


# --------------------------------------------------------------------------- #
# Merge with existing projection row                                           #
# --------------------------------------------------------------------------- #
# Mobile 增量 push 只带部分字段(比如只改 name),不带的字段要保留现有值,
# 不能被默认值(0 / None / 空字符串)覆盖。所以写 projection 前先拉已有
# 行,payload 值为 None 的 key 用旧值补齐,再 upsert。
#
# spec 的 fields 是 [(payload_key, projection 列名)] 或
# [(payload_key, projection 列名, transform_fn)]。transform 处理 tx 的
# json 文本列 / datetime isoformat 这种需要格式转换的情况。


def _json_loads_safe(value: Any) -> Any:
    """把 DB 里存的 JSON 字符串列反序列化回 Python 对象。解析失败返回 None。

    projection 里 ``tag_sync_ids_json`` / ``attachments_json`` 是存成文本的
    JSON array。返回给 mobile 做 merge 时要是原生 list,不能直接塞字符串。
    """
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _isoformat_or_none(value: Any) -> Optional[str]:
    """datetime → ISO8601 字符串。None 直接返回。"""
    return value.isoformat() if value else None


# _FieldSpec 的形态二选一:
#   (payload_key, projection_column_name)                       — 直接 getattr
#   (payload_key, projection_column_name, transform_callable)   — 经 transform
_FieldSpec = tuple  # 运行时就是元组,类型系统上表达不了两种 arity


class _MergeSpec:
    """每个 entity_type 的 projection 字段映射 + 对应的 SQLAlchemy model。"""

    __slots__ = ("model", "fields")

    def __init__(self, model: type, fields: list[_FieldSpec]):
        self.model = model
        self.fields = fields


# user-scope merge specs:用 (user_id, sync_id) 当主键检 existing row。
_USER_MERGE_SPECS: dict[str, _MergeSpec] = {
    "account": _MergeSpec(UserAccountProjection, [
        ("syncId", "sync_id"),
        ("name", "name"),
        ("type", "account_type"),
        ("currency", "currency"),
        ("initialBalance", "initial_balance"),
        # 扩展字段:跟 mobile lib/data/db.dart Account 表对齐,sync_engine 已经
        # 在 push 这些字段(driftCamel),server 现在落库 + round-trip。
        ("note", "note"),
        ("creditLimit", "credit_limit"),
        ("billingDay", "billing_day"),
        ("paymentDueDay", "payment_due_day"),
        ("bankName", "bank_name"),
        ("cardLastFour", "card_last_four"),
        # 主帳戶(合併帳單,§2.9 Phase 4 MOZE_FEATURE_GAP_SD.md):子卡指向主卡
        # 的 sync_id,None = 沒有掛靠。
        ("parentAccountId", "parent_account_id"),
        # 自動扣繳(§2.9,2026-08-04 改版):開關 + 來源帳戶 sync_id。
        ("autoPayEnabled", "auto_pay_enabled"),
        ("autoPayFromAccountId", "auto_pay_from_account_id"),
        # 账户隐藏(issue #240):payload 键 hidden(camelCase 同名,跟 App
        # serializeAccount 对齐)。缺键时 _merge_from_spec 从 existing 行补齐,
        # 不被 partial-update 冲成 False(merge 契约测试见
        # tests/test_account_hidden_sync.py::test_mobile_push_account_partial_update_keeps_hidden)。
        ("hidden", "hidden"),
        # 帳戶頭像(2026-08-02 補強):None/空字串 = 沒有自訂頭像。
        ("avatarCloudFileId", "avatar_cloud_file_id"),
        ("avatarCloudSha256", "avatar_cloud_sha256"),
        # SwipeSmart 卡片對照(Phase 14):None/空字串 = 沒有對照。
        ("swipesmartCardId", "swipesmart_card_id"),
        # 納入總餘額(Phase 18):缺鍵時 _merge_from_spec 從 existing 行補齊,
        # 不被 partial-update 沖成 False。
        ("includeInTotal", "include_in_total"),
        # 帳戶清單拖曳排序(2026-09-05):缺鍵時 _merge_from_spec 從 existing
        # 行補齊,不被 partial-update 沖成 None。
        ("sortOrder", "sort_order"),
        # 股票持股(2026-09-28):投資理財帳戶費用設定物件,落 TEXT 列,merge
        # 時 _json_loads_safe 轉回 dict,跟 recurring_rule.advancedRuleJson 同款。
        ("investmentSettings", "investment_settings_json", _json_loads_safe),
    ]),
    "exchange_rate_override": _MergeSpec(UserExchangeRateProjection, [
        ("syncId", "sync_id"),
        ("baseCurrency", "base_currency"),
        ("quoteCurrency", "quote_currency"),
        ("rate", "rate"),
        ("updatedAt", "updated_at", _isoformat_or_none),
    ]),
    "category": _MergeSpec(UserCategoryProjection, [
        ("syncId", "sync_id"),
        ("name", "name"),
        ("kind", "kind"),
        ("level", "level"),
        ("sortOrder", "sort_order"),
        ("icon", "icon"),
        ("iconType", "icon_type"),
        ("customIconPath", "custom_icon_path"),
        ("iconCloudFileId", "icon_cloud_file_id"),
        ("iconCloudSha256", "icon_cloud_sha256"),
        ("parentName", "parent_name"),
        ("color", "color"),
    ]),
    "tag": _MergeSpec(UserTagProjection, [
        ("syncId", "sync_id"),
        ("name", "name"),
        ("color", "color"),
    ]),
    "card_reward_rule": _MergeSpec(ReadCardRewardRuleProjection, [
        ("syncId", "sync_id"),
        ("accountId", "account_sync_id"),
        ("label", "label"),
        ("categoryIds", "category_sync_ids_json", _json_loads_safe),
        ("rateType", "rate_type"),
        ("rateValue", "rate_value"),
        ("rounding", "rounding"),
        ("totalRounding", "total_rounding"),
        ("calcBasis", "calc_basis"),
        ("interval", "interval"),
        ("minSpendThreshold", "min_spend_threshold"),
        ("minTxAmount", "min_tx_amount"),
        ("capAmount", "cap_amount"),
        ("capSharedKey", "cap_shared_key"),
        ("startsAt", "starts_at", _isoformat_or_none),
        ("endsAt", "ends_at", _isoformat_or_none),
        ("settlementType", "settlement_type"),
        ("settlementDays", "settlement_days"),
        ("settlementMonthOffset", "settlement_month_offset"),
        ("settlementDayOfMonth", "settlement_day_of_month"),
        ("rewardAccountId", "reward_account_id"),
        ("note", "note"),
        ("enabled", "enabled"),
    ]),
}


# ledger-scope merge specs:用 (ledger_id, sync_id) 当主键检 existing row。
_LEDGER_MERGE_SPECS: dict[str, _MergeSpec] = {
    "budget": _MergeSpec(ReadBudgetProjection, [
        ("syncId", "sync_id"),
        ("type", "budget_type"),
        ("categoryId", "category_sync_id"),
        ("amount", "amount"),
        ("period", "period"),
        ("startDay", "start_day"),
        ("enabled", "enabled"),
    ]),
    "recurring_rule": _MergeSpec(ReadRecurringRuleProjection, [
        ("syncId", "sync_id"),
        ("txType", "tx_type"),
        ("amount", "amount"),
        ("note", "note"),
        ("categoryId", "category_sync_id"),
        ("accountId", "account_sync_id"),
        ("fromAccountId", "from_account_sync_id"),
        ("toAccountId", "to_account_sync_id"),
        # Phase 24:商家/專案/標籤,搭配「連同未來週期」批次轉發。
        ("merchant", "merchant"),
        ("projectId", "project_sync_id"),
        ("tagIds", "tag_sync_ids_json", _json_loads_safe),
        ("frequency", "frequency"),
        ("interval", "interval"),
        ("nextRunAt", "next_run_at", _isoformat_or_none),
        ("endAt", "end_at", _isoformat_or_none),
        ("enabled", "enabled"),
        # Phase 1.5(§2.12.2):视窗续产生进度 + 进阶规则。advancedRuleJson 落库
        # 是 TEXT 列(json.dumps,见 projection.upsert_recurring_rule),merge
        # 时用 _json_loads_safe 转回 dict,跟 tagIds/attachments 同一套惯例。
        ("generatedUntilAt", "generated_until_at", _isoformat_or_none),
        ("advancedRuleJson", "advanced_rule_json", _json_loads_safe),
        # 手續費/折扣/信用卡回饋(2026-08 使用者回饋):規則固定屬性,搭配
        # 「連同未來週期」批次更新一起轉發,同 transaction merge spec 同名
        # 欄位寫法。
        ("baseAmount", "base_amount"),
        ("feeAmount", "fee_amount"),
        ("feeLabel", "fee_label"),
        ("discountAmount", "discount_amount"),
        ("discountLabel", "discount_label"),
        ("rewardRuleIds", "reward_rule_sync_ids_json", _json_loads_safe),
    ]),
    "installment_plan": _MergeSpec(ReadInstallmentPlanProjection, [
        ("syncId", "sync_id"),
        ("totalAmount", "total_amount"),
        ("periods", "periods"),
        ("periodAmount", "period_amount"),
        ("firstPeriodAt", "first_period_at", _isoformat_or_none),
        ("nextPeriodAt", "next_period_at", _isoformat_or_none),
        ("paidPeriods", "paid_periods"),
        ("accountId", "account_sync_id"),
        ("categoryId", "category_sync_id"),
        ("note", "note"),
        ("status", "status"),
        # Phase 1.5(§2.12.1)攤還算法参数。
        ("repaymentMethod", "repayment_method"),
        ("interestPeriod", "interest_period"),
        ("interestRate", "interest_rate"),
        ("roundAmounts", "round_amounts"),
        ("remainderPosition", "remainder_position"),
        ("gracePeriodMonths", "grace_period_months"),
        ("offsetBreakdownJson", "offset_breakdown_json"),
    ]),
    "installment_period": _MergeSpec(ReadInstallmentPeriodProjection, [
        ("syncId", "sync_id"),
        ("planId", "plan_sync_id"),
        ("periodNo", "period_no"),
        ("dueAt", "due_at", _isoformat_or_none),
        ("principalAmount", "principal_amount"),
        ("interestAmount", "interest_amount"),
        ("totalAmount", "total_amount"),
        ("status", "status"),
        ("txId", "tx_sync_id"),
    ]),
    "debt": _MergeSpec(ReadDebtProjection, [
        ("syncId", "sync_id"),
        ("direction", "direction"),
        ("counterpartyName", "counterparty_name"),
        ("principalAmount", "principal_amount"),
        ("dueAt", "due_at", _isoformat_or_none),
        ("note", "note"),
        ("closedAt", "closed_at", _isoformat_or_none),
        ("categoryId", "category_sync_id"),
        ("originTxId", "origin_tx_sync_id"),
        ("excludedFromTotal", "excluded_from_total"),
    ]),
    # 股票交易明細(2026-09-28,docs/STOCK_HOLDINGS_SD.md)。對齊 App
    # lib/cloud/sync/entity_serializer.dart::serializeStockTrade。
    "stock_trade": _MergeSpec(ReadStockTradeProjection, [
        ("syncId", "sync_id"),
        ("accountId", "account_sync_id"),
        ("market", "market"),
        ("symbol", "symbol"),
        ("securityName", "security_name"),
        ("tradeType", "trade_type"),
        ("shares", "shares"),
        ("price", "price"),
        ("fee", "fee"),
        ("tax", "tax"),
        ("amount", "amount"),
        ("currency", "currency"),
        ("tradeDate", "trade_date", _isoformat_or_none),
        ("txId", "tx_sync_id"),
        ("dividendEventRef", "dividend_event_ref"),
        ("note", "note"),
        ("createdByUserId", "created_by_user_id"),
    ]),
    "project": _MergeSpec(ReadProjectProjection, [
        ("syncId", "sync_id"),
        ("name", "name"),
        ("icon", "icon"),
        ("budgetAmount", "budget_amount"),
        ("periodType", "period_type"),
        ("periodStart", "period_start"),
        ("periodEnd", "period_end"),
        ("carryoverEnabled", "carryover_enabled"),
        ("visibleOnHome", "visible_on_home"),
        ("enabled", "enabled"),
        ("sortOrder", "sort_order"),
        ("incomeIncludedInBudget", "income_included_in_budget"),
        ("dailyBudgetEnabled", "daily_budget_enabled"),
        ("dailyBudgetMode", "daily_budget_mode"),
        ("reminderThresholdPercent", "reminder_threshold_percent"),
    ]),
    "project_category_budget": _MergeSpec(ReadProjectCategoryBudgetProjection, [
        ("syncId", "sync_id"),
        ("projectId", "project_sync_id"),
        ("categoryId", "category_sync_id"),
        ("mode", "mode"),
        ("fixedAmount", "fixed_amount"),
        ("percentage", "percentage"),
        ("carryoverEnabled", "carryover_enabled"),
        ("sortOrder", "sort_order"),
    ]),
    "tx_template": _MergeSpec(ReadTxTemplateProjection, [
        ("syncId", "sync_id"),
        ("name", "name"),
        ("txType", "tx_type"),
        ("amount", "amount"),
        ("note", "note"),
        ("categoryId", "category_sync_id"),
        ("accountId", "account_sync_id"),
        ("fromAccountId", "from_account_sync_id"),
        ("toAccountId", "to_account_sync_id"),
        ("tagIds", "tag_sync_ids_json", _json_loads_safe),
        ("sortOrder", "sort_order"),
    ]),
    "transaction": _MergeSpec(ReadTxProjection, [
        ("syncId", "sync_id"),
        ("type", "tx_type"),
        ("amount", "amount"),
        ("happenedAt", "happened_at", _isoformat_or_none),
        ("note", "note"),
        ("merchant", "merchant"),
        ("categoryId", "category_sync_id"),
        ("categoryName", "category_name"),
        ("categoryKind", "category_kind"),
        ("accountId", "account_sync_id"),
        ("accountName", "account_name"),
        ("fromAccountId", "from_account_sync_id"),
        ("fromAccountName", "from_account_name"),
        ("toAccountId", "to_account_sync_id"),
        ("toAccountName", "to_account_name"),
        ("tags", "tags_csv"),
        ("tagIds", "tag_sync_ids_json", _json_loads_safe),
        ("attachments", "attachments_json", _json_loads_safe),
        ("txIndex", "tx_index"),
        ("createdByUserId", "created_by_user_id"),
        # 共享账本 Phase 1:updatedByUserId 也回写到 projection,作为
        # last_edited_by。snapshot_mutator._mark_entity_actor 写入。
        ("updatedByUserId", "last_edited_by_user_id"),
        # 账单标记(.docs/transaction-flags)。merge 时缺键保留既有行值,
        # 布尔强转在 projection.upsert_tx(default=False)做。
        ("excludeFromStats", "exclude_from_stats"),
        ("excludeFromBudget", "exclude_from_budget"),
        # 交易级多币种(0018):缺键保留既有折算快照(旧 App 改备注不丢折算);
        # payload 带 amount 不带 nativeAmount 时的联动缩放见
        # _sync_native_amount_after_merge。
        ("currencyCode", "currency_code"),
        ("nativeAmount", "native_amount"),
        # 跨幣別轉帳(2026-08):缺键保留既有轉入金額快照;payload 带 amount
        # 不带 toAmount 时的联动缩放见 _sync_to_amount_after_merge。
        ("toAmount", "to_amount"),
        # 手續費/折扣(2026-08 使用者需求):缺键保留既有值,同 nativeAmount
        # 惯例。base_amount 是信用卡回饋計算的權威基準(見
        # services/card_rewards.py::_reward_base_amount)。
        ("baseAmount", "base_amount"),
        ("feeAmount", "fee_amount"),
        ("feeLabel", "fee_label"),
        ("discountAmount", "discount_amount"),
        ("discountLabel", "discount_label"),
        ("refundOfId", "refund_of_sync_id"),
        ("installmentPlanId", "installment_plan_sync_id"),
        # 週期性收支(§2.12.2 Phase 1.5)反查字段 + 单笔编辑标记。
        ("recurringRuleId", "recurring_rule_sync_id"),
        ("recurringOccurrenceOverridden", "recurring_occurrence_overridden"),
        # 借還款追蹤(§2.5 Phase 3)反查字段,同款语义。
        ("debtId", "debt_sync_id"),
        # 專案(Phase 13)反查字段,同款语义。
        ("projectId", "project_sync_id"),
        # 信用卡紅利回饋(§2.9.5,2026-08-06 改版)反查字段,同款语义:缺键
        # 保留既有勾选列表。
        ("rewardRuleIds", "reward_rule_sync_ids_json", _json_loads_safe),
        # 信用卡紅利回饋自動入帳(§2.9.5.4 補強)反查字段,同款语义。
        ("rewardSourceTxId", "reward_source_tx_sync_id"),
        # 拆帳(§2.4):缺键保留既有 splits(跟 attachments 同一套惯例)——
        # payload 没带 "splits" key(旧客户端 / 只改其它字段的 push)时,merge
        # 从 existing.splits_json 补回旧列表;projection.upsert_tx 再据此整批
        # delete-then-insert read_tx_split_projection。
        ("splits", "splits_json", _json_loads_safe),
        # 延後入帳(§2.10 Phase 5):缺键保留既有标记,同款语义。
        ("deferredPostingAt", "deferred_posting_at", _isoformat_or_none),
        # 對帳模式(§2.10,2026-08-09 改版):缺键保留既有核对状态,同款语义。
        ("reconciledAt", "reconciled_at", _isoformat_or_none),
    ]),
}


# user-scope upsert dispatch:apply_user_change_to_projection 用。
_USER_UPSERT_DISPATCH: dict[str, Callable] = {
    "account": projection.upsert_account,
    "category": projection.upsert_category,
    "tag": projection.upsert_tag,
    "exchange_rate_override": projection.upsert_exchange_rate_override,
    "card_reward_rule": projection.upsert_card_reward_rule,
}


# ledger-scope upsert dispatch:apply_change_to_projection 用。
_LEDGER_UPSERT_DISPATCH: dict[str, Callable] = {
    "budget": projection.upsert_budget,
    "transaction": projection.upsert_tx,
    "recurring_rule": projection.upsert_recurring_rule,
    "installment_plan": projection.upsert_installment_plan,
    "installment_period": projection.upsert_installment_period,
    "debt": projection.upsert_debt,
    "project": projection.upsert_project,
    "project_category_budget": projection.upsert_project_category_budget,
    "tx_template": projection.upsert_tx_template,
    "stock_trade": projection.upsert_stock_trade,
}


# Delete 路径分两组。
#
# ledger-scope:tx / budget,handler 签名 ``(db, ledger_id, sync_id, user_id)``。
# user-scope:account / category / tag,handler 签名 ``(db, user_id, sync_id)``。
#
# tx 删除时附带 GC tx 附件(走 user_id scope —— attachment GC 已重构);
# category 删除时附带 GC icon 附件(同样 user_id scope)。


def _compact_entity_upsert_events(
    db: Session,
    *,
    user_id: str,
    entity_type: str,
    entity_sync_id: str,
) -> int:
    """实体被删除后,清掉该 (user_id, entity_type, entity_sync_id) 的全部 **upsert**
    sync_changes 历史,**保留 delete 事件本身**。

    背景:sync_changes 一般是 append-only event log,不裸 DELETE(防止 projection
    跟 log 漂移,见 21136 案例)。但**实体彻底下线**的场景下,所有 upsert 事件都已
    没有价值:
      - projection 已经把该实体的行删了 → 没人会再引用任何 source_change_id
      - 其它设备 cursor 落后时,只看到 delete event 也能正确处理(apply 路径
        `if (existingId == null) return;` 是 idempotent no-op,见
        sync_engine_apply.dart::_applyTransactionChange)
      - delete event 本身**保留**,确保 cursor 落后的设备能 apply delete 把本地
        副本删干净

    所以"删 entity 时连带清掉它的 upsert 历史"是契约的**合理例外**,跟"projection
    漂移"那种裸 DELETE 不一样。

    返回清掉的 row 数。
    """
    result = db.execute(
        sa_delete(SyncChange).where(
            SyncChange.user_id == user_id,
            SyncChange.entity_type == entity_type,
            SyncChange.entity_sync_id == entity_sync_id,
            SyncChange.action != "delete",
        )
    )
    return result.rowcount or 0


def _delete_tx(db: Session, ledger_id: str, sync_id: str, user_id: str) -> None:
    # 先收集附件 fileId(删行后 attachments_json 就没了)再删 tx,然后 GC
    # 孤立附件。共享引用(同图多 tx)的会自动保留。
    tx_file_ids = projection.collect_tx_attachment_fileids(
        db, ledger_id=ledger_id, sync_id=sync_id,
    )
    projection.delete_tx(db, ledger_id=ledger_id, sync_id=sync_id)
    # 共享账本场景:attachment.user_id 可能是 Editor 而 SyncChange.user_id 是
    # ledger owner,GC 必须按 ledger_id scope 而不是 user_id —— 否则 Editor 上传
    # 的附件永远被静默跳过留作孤儿。详见 projection.gc_orphan_attachments_for_ledger
    # 的 doc。
    projection.gc_orphan_attachments_for_ledger(
        db, ledger_id=ledger_id, file_ids=tx_file_ids,
    )
    # 实体彻底下线,清 upsert 历史(详见 _compact_entity_upsert_events)
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="transaction",
        entity_sync_id=sync_id,
    )


def _delete_user_category(db: Session, user_id: str, sync_id: str) -> None:
    # 删 user_category_projection 行,再 GC 自身 + 子分类的图标附件。
    cat_file_ids = projection.collect_category_icon_fileids(
        db, user_id=user_id, sync_id=sync_id,
    )
    projection.delete_category(db, user_id=user_id, sync_id=sync_id)
    # category icon 是 user-scope,继续走 gc_orphan_attachments(user_id)
    projection.gc_orphan_attachments(
        db, user_id=user_id, file_ids=cat_file_ids,
    )
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="category", entity_sync_id=sync_id,
    )


def _delete_budget(db: Session, ledger_id: str, sync_id: str, user_id: str) -> None:
    projection.delete_budget(db, ledger_id=ledger_id, sync_id=sync_id)
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="budget", entity_sync_id=sync_id,
    )


def _delete_recurring_rule(db: Session, ledger_id: str, sync_id: str, user_id: str) -> None:
    projection.delete_recurring_rule(db, ledger_id=ledger_id, sync_id=sync_id)
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="recurring_rule", entity_sync_id=sync_id,
    )


def _delete_installment_plan(db: Session, ledger_id: str, sync_id: str, user_id: str) -> None:
    projection.delete_installment_plan(db, ledger_id=ledger_id, sync_id=sync_id)
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="installment_plan", entity_sync_id=sync_id,
    )


def _delete_installment_period(db: Session, ledger_id: str, sync_id: str, user_id: str) -> None:
    projection.delete_installment_period(db, ledger_id=ledger_id, sync_id=sync_id)
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="installment_period", entity_sync_id=sync_id,
    )


def _delete_debt(db: Session, ledger_id: str, sync_id: str, user_id: str) -> None:
    projection.delete_debt(db, ledger_id=ledger_id, sync_id=sync_id)
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="debt", entity_sync_id=sync_id,
    )


def _delete_project(db: Session, ledger_id: str, sync_id: str, user_id: str) -> None:
    projection.delete_project(db, ledger_id=ledger_id, sync_id=sync_id)
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="project", entity_sync_id=sync_id,
    )


def _delete_project_category_budget(db: Session, ledger_id: str, sync_id: str, user_id: str) -> None:
    projection.delete_project_category_budget(db, ledger_id=ledger_id, sync_id=sync_id)
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="project_category_budget", entity_sync_id=sync_id,
    )


def _delete_tx_template(db: Session, ledger_id: str, sync_id: str, user_id: str) -> None:
    projection.delete_tx_template(db, ledger_id=ledger_id, sync_id=sync_id)
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="tx_template", entity_sync_id=sync_id,
    )


def _delete_stock_trade(db: Session, ledger_id: str, sync_id: str, user_id: str) -> None:
    projection.delete_stock_trade(db, ledger_id=ledger_id, sync_id=sync_id)
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="stock_trade", entity_sync_id=sync_id,
    )


def _delete_user_account(db: Session, user_id: str, sync_id: str) -> None:
    projection.delete_account(db, user_id=user_id, sync_id=sync_id)
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="account", entity_sync_id=sync_id,
    )


def _delete_user_tag(db: Session, user_id: str, sync_id: str) -> None:
    projection.delete_tag(db, user_id=user_id, sync_id=sync_id)
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="tag", entity_sync_id=sync_id,
    )


def _delete_user_exchange_rate_override(db: Session, user_id: str, sync_id: str) -> None:
    projection.delete_exchange_rate_override(db, user_id=user_id, sync_id=sync_id)
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="exchange_rate_override", entity_sync_id=sync_id,
    )


def _delete_user_card_reward_rule(db: Session, user_id: str, sync_id: str) -> None:
    projection.delete_card_reward_rule(db, user_id=user_id, sync_id=sync_id)
    _compact_entity_upsert_events(
        db, user_id=user_id, entity_type="card_reward_rule", entity_sync_id=sync_id,
    )


_LEDGER_DELETE_DISPATCH: dict[str, Callable[[Session, str, str, str], None]] = {
    "transaction": _delete_tx,
    "budget": _delete_budget,
    "recurring_rule": _delete_recurring_rule,
    "installment_plan": _delete_installment_plan,
    "installment_period": _delete_installment_period,
    "debt": _delete_debt,
    "project": _delete_project,
    "project_category_budget": _delete_project_category_budget,
    "tx_template": _delete_tx_template,
    "stock_trade": _delete_stock_trade,
}


_USER_DELETE_DISPATCH: dict[str, Callable[[Session, str, str], None]] = {
    "account": _delete_user_account,
    "category": _delete_user_category,
    "tag": _delete_user_tag,
    "exchange_rate_override": _delete_user_exchange_rate_override,
    "card_reward_rule": _delete_user_card_reward_rule,
}


# --------------------------------------------------------------------------- #
# Rename cascade                                                               #
# --------------------------------------------------------------------------- #
# account / category / tag 的 name 变了之后,ReadTxProjection 里用作 denorm
# 列的 account_name / category_name / tags_csv 也要一起刷。必须在 upsert
# 当前实体 **之前** 跑 —— cascade 按 *旧名* 找 tx 行 UPDATE,upsert 之后
# 旧名就丢了。


def _detect_and_run_rename_cascade_user(
    db: Session,
    *,
    entity_type: str,
    user_id: str,
    sync_id: str,
    payload: dict,
) -> None:
    """user-global rename cascade:探测 name 变化,刷遍该用户所有 ledger 的
    read_tx_projection denorm 列。account / category / tag 三种。"""
    new_name = str(payload.get("name") or "").strip()
    if not new_name:
        return

    if entity_type == "account":
        prev_row = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == user_id,
                UserAccountProjection.sync_id == sync_id,
            )
        )
        old_name = (prev_row.name or "").strip() if prev_row is not None else ""
        if old_name and old_name != new_name:
            projection.rename_cascade_account(
                db, user_id=user_id, account_sync_id=sync_id, new_name=new_name,
            )
    elif entity_type == "category":
        prev_row = db.scalar(
            select(UserCategoryProjection).where(
                UserCategoryProjection.user_id == user_id,
                UserCategoryProjection.sync_id == sync_id,
            )
        )
        old_name = (prev_row.name or "").strip() if prev_row is not None else ""
        if old_name and old_name != new_name:
            projection.rename_cascade_category(
                db, user_id=user_id, category_sync_id=sync_id,
                new_name=new_name,
                new_kind=str(payload.get("kind") or "").strip() or None,
            )
    elif entity_type == "tag":
        prev_row = db.scalar(
            select(UserTagProjection).where(
                UserTagProjection.user_id == user_id,
                UserTagProjection.sync_id == sync_id,
            )
        )
        old_name = (prev_row.name or "").strip() if prev_row is not None else ""
        if old_name and old_name != new_name:
            projection.rename_cascade_tag(
                db, user_id=user_id, tag_sync_id=sync_id,
                old_name=old_name, new_name=new_name,
            )


# --------------------------------------------------------------------------- #
# Merge                                                                        #
# --------------------------------------------------------------------------- #


def _merge_from_spec(spec: _MergeSpec, existing, payload: dict) -> dict:
    """从 existing row + spec.fields 构造 base dict,再把 payload 里「有出現的
    key」叠加上来。两条 merge 路径(ledger / user)共用这段。

    2026-08-18 修正:叠加条件曾经是 `v is not None`,没法区分「payload 没带
    这个 key(缺键,维持旧值)」跟「payload 带了这个 key 但值是显式
    None(使用者主动清空这个欄位)」——两种情况的 value 在 dict 里都读不到
    非-None 值,旧写法会把显式清空也当成缺键处理,导致 base(existing row 的
    旧值)一路原样穿透回写,清空动作在这条 merge 路径上被静默吞掉。对齐
    `snapshot_mutator.py::update_transaction` 已经在用的 `"key" in payload`
    写法——只按 key 是否出现决定要不要覆盖,不看值是不是 None。这条路径是
    `/sync/push`(mobile)的 projection apply 共用的,受影响的不只是
    reconciledAt/deferredPostingAt,凡是 `_LEDGER_MERGE_SPECS`/
    `_USER_MERGE_SPECS` 里登记的欄位,只要能被使用者显式清空,mobile 端清空
    都会撞到同一个 bug。"""
    merged: dict = {}
    for spec_tuple in spec.fields:
        if len(spec_tuple) == 3:
            payload_key, db_attr, transform = spec_tuple
        else:
            payload_key, db_attr = spec_tuple
            transform = None
        if payload_key in payload:
            merged[payload_key] = payload[payload_key]
        else:
            value = getattr(existing, db_attr)
            if transform is not None:
                value = transform(value)
            merged[payload_key] = value
    return merged


def _sync_native_amount_after_merge(existing, payload: dict, merged: dict) -> dict:
    """交易级多币种(0018):payload 带新 amount 但不带 nativeAmount(旧客户端
    只知道原币金额)时,merge 从 existing 补回的旧 native_amount 会与新 amount
    失配(账本统计显示旧折算值)。联动规则(与 snapshot_mutator L14 一致):

    - amount 未变 → 保留旧折算(merge 已补,即快照保护,不动)
    - 同币种 / 未折算(old_native == old_amount,隐含汇率 1)→ 跟随新 amount
    - 外币 → 按该笔隐含汇率等比缩放(保持记账时汇率)
    - old_amount == 0 无法推汇率 → 退化 = 新 amount(1:1,App 端 L11 可捞回)
    """
    if payload.get("amount") is None or payload.get("nativeAmount") is not None:
        return merged
    old_native = getattr(existing, "native_amount", None)
    if old_native is None:
        return merged
    try:
        new_amount = float(payload["amount"])
        old_amount = float(getattr(existing, "amount", 0.0) or 0.0)
    except (TypeError, ValueError):
        return merged
    if new_amount == old_amount:
        return merged
    from .snapshot_mutator import rescale_native_amount

    merged["nativeAmount"] = rescale_native_amount(
        old_amount, old_native, new_amount)
    return merged


def _sync_to_amount_after_merge(existing, payload: dict, merged: dict) -> dict:
    """跨幣別轉帳(2026-08):結構完全比照 {@link _sync_native_amount_after_merge}
    ——payload 带新 amount(轉出金額)但不带 toAmount(舊客戶端只知道轉出端)
    時,merge 從 existing 補回的舊 to_amount 會與新 amount 失配。只在既有
    row 本身就是轉帳(tx_type=="transfer")且已有 to_amount 快照時才聯動,
    非轉帳 / 同幣別轉帳(to_amount 為 None)不受影響。
    """
    if getattr(existing, "tx_type", None) != "transfer":
        return merged
    if payload.get("amount") is None or payload.get("toAmount") is not None:
        return merged
    old_to = getattr(existing, "to_amount", None)
    if old_to is None:
        return merged
    try:
        new_amount = float(payload["amount"])
        old_amount = float(getattr(existing, "amount", 0.0) or 0.0)
    except (TypeError, ValueError):
        return merged
    if new_amount == old_amount:
        return merged
    from .snapshot_mutator import rescale_native_amount

    merged["toAmount"] = rescale_native_amount(
        old_amount, old_to, new_amount)
    return merged


def merge_with_existing(
    db: Session,
    entity_type: str,
    ledger_id: str,
    sync_id: str,
    payload: dict,
) -> dict:
    """**ledger-scope** merge:查 projection 已有行,payload 缺键的字段用旧值
    补齐;payload 带键的字段(含显式 None,代表使用者主动清空)照写。
    entity_type 未登记在 _LEDGER_MERGE_SPECS 时(比如 'ledger' 自己),直接
    返回 payload 不做处理。"""
    spec = _LEDGER_MERGE_SPECS.get(entity_type)
    if spec is None:
        return payload
    existing = db.scalar(
        select(spec.model).where(
            spec.model.ledger_id == ledger_id,
            spec.model.sync_id == sync_id,
        )
    )
    if existing is None:
        return payload
    merged = _merge_from_spec(spec, existing, payload)
    if entity_type == "transaction":
        merged = _sync_native_amount_after_merge(existing, payload, merged)
        merged = _sync_to_amount_after_merge(existing, payload, merged)
    return merged


def merge_with_existing_user(
    db: Session,
    entity_type: str,
    user_id: str,
    sync_id: str,
    payload: dict,
) -> dict:
    """**user-scope** merge:查 user_*_projection 已有行,payload 缺键的字段
    补齐,带键的字段(含显式 None)照写——语意同 [merge_with_existing]。"""
    spec = _USER_MERGE_SPECS.get(entity_type)
    if spec is None:
        return payload
    existing = db.scalar(
        select(spec.model).where(
            spec.model.user_id == user_id,
            spec.model.sync_id == sync_id,
        )
    )
    if existing is None:
        return payload
    return _merge_from_spec(spec, existing, payload)


# --------------------------------------------------------------------------- #
# Top-level entry                                                              #
# --------------------------------------------------------------------------- #


def apply_change_to_projection(
    db: Session,
    *,
    ledger_id: str,
    ledger_owner_id: str,
    change: SyncChange,
) -> None:
    """把一条 **ledger-scope** SyncChange 投到 projection 上。
    (user-global 走 ``apply_user_change_to_projection``。)

    流程:

      1. ledger entity:更新 Ledger 表的 name / currency(snapshot 已废弃)。
      2. delete action:按 entity 类型清理对应 projection 行 + 附加资源。
      3. upsert action:
         a. parse payload → dict,注入 syncId
         b. merge_with_existing 把 payload 缺失 / None 的字段补齐
         c. _LEDGER_UPSERT_DISPATCH 写入对应 projection 表

    ``change.change_id`` 作为 ``source_change_id`` 写进行,诊断用:后续要是
    发现某行数据不对,查这一列能定位是哪次 materialize 落的。

    防御性:若 entity_type 是 user-global(category/account/tag),调用方走错
    路径 — 抛 AssertionError 让 caller 修(应该走 apply_user_change_to_projection)。
    """
    assert change.entity_type not in USER_GLOBAL_ENTITY_TYPES, (
        f"apply_change_to_projection 收到 user-global entity {change.entity_type},"
        f"应该走 apply_user_change_to_projection(change_id={change.change_id})"
    )

    # --- ledger entity(特殊:不是 projection 表,直接改 Ledger) --------- #
    if change.entity_type == "ledger":
        if change.action == "delete":
            return
        payload_raw = _parse_payload(change.payload_json)
        if payload_raw is None:
            return
        new_name = payload_raw.get("ledgerName")
        new_currency = payload_raw.get("currency")
        new_month_start_day = payload_raw.get("monthStartDay")
        ledger_row = db.scalar(select(Ledger).where(Ledger.id == ledger_id))
        if ledger_row is not None:
            if isinstance(new_name, str) and new_name.strip():
                ledger_row.name = new_name.strip()
            if isinstance(new_currency, str) and new_currency.strip():
                ledger_row.currency = new_currency.strip()[:16]
            # bool 是 int 子类,显式排除;key 缺失时不动(partial-update merge 契约)
            if isinstance(new_month_start_day, int) and not isinstance(new_month_start_day, bool):
                ledger_row.month_start_day = max(1, min(28, new_month_start_day))
        return

    sync_id = change.entity_sync_id

    # --- delete --------------------------------------------------------- #
    if change.action == "delete":
        handler = _LEDGER_DELETE_DISPATCH.get(change.entity_type)
        if handler is not None:
            handler(db, ledger_id, sync_id, ledger_owner_id)
        return

    # --- upsert --------------------------------------------------------- #
    payload = _parse_payload(change.payload_json)
    if payload is None:
        return
    payload.setdefault("syncId", sync_id)
    # 共享账本兜底:mobile EntitySerializer.serializeTransaction 当前不把
    # updatedByUserId 写进 payload,直接 push 上来 projection 这字段会是 NULL。
    # 从 SyncChange.updated_by_user_id(推送方真实身份)兜底注入。
    #
    # **不**兜底 createdByUserId — 那会让"B 编辑 A 创建的 tx"路径误把 created
    # 写成 B。createdByUserId 由 projection.upsert_tx 内部 COALESCE 保留旧值
    # (见 upsert_tx 注释)。
    actor = change.updated_by_user_id
    if actor:
        payload.setdefault("updatedByUserId", actor)

    if change.entity_type in _LEDGER_MERGE_SPECS:
        merged = merge_with_existing(db, change.entity_type, ledger_id, sync_id, payload)
        _LEDGER_UPSERT_DISPATCH[change.entity_type](
            db,
            ledger_id=ledger_id,
            user_id=ledger_owner_id,
            source_change_id=change.change_id,
            payload=merged,
        )


def apply_user_change_to_projection(
    db: Session,
    *,
    user_id: str,
    change: SyncChange,
) -> None:
    """把一条 **user-scope** SyncChange 投到 user_*_projection 上。

    流程跟 ledger-scope 对偶,但:
      - 主键查 / 写都按 (user_id, sync_id),跟账本无关
      - rename cascade 也按 user_id 跨该用户所有 ledger 刷 read_tx_projection

    防御性:entity_type 必须在 USER_GLOBAL_ENTITY_TYPES,否则 caller 错路径。
    """
    assert change.entity_type in USER_GLOBAL_ENTITY_TYPES, (
        f"apply_user_change_to_projection 收到非 user-global entity "
        f"{change.entity_type}(change_id={change.change_id})"
    )

    sync_id = change.entity_sync_id

    # --- delete --------------------------------------------------------- #
    if change.action == "delete":
        handler = _USER_DELETE_DISPATCH.get(change.entity_type)
        if handler is not None:
            handler(db, user_id, sync_id)
        return

    # --- upsert --------------------------------------------------------- #
    payload = _parse_payload(change.payload_json)
    if payload is None:
        return
    payload.setdefault("syncId", sync_id)

    # rename cascade 必须先于 upsert 当前实体 —— 用的是"旧名" match tx 行。
    _detect_and_run_rename_cascade_user(
        db,
        entity_type=change.entity_type,
        user_id=user_id,
        sync_id=sync_id,
        payload=payload,
    )

    if change.entity_type in _USER_MERGE_SPECS:
        merged = merge_with_existing_user(
            db, change.entity_type, user_id, sync_id, payload,
        )
        # 分类 icon 兜底:老 App(Flutter 3.0 及之前)可能推空 icon 的 category。
        # 写进 projection 前按分类名字 byName 推一次,跟 alembic 0002 backfill
        # 对齐,避免 web 端继续看到兜底图。Flutter 3.0.1 做完 write-time
        # migration 后这段可以退役。
        if change.entity_type == "category":
            icon_val = merged.get("icon") if isinstance(merged, dict) else None
            if icon_val is None or (isinstance(icon_val, str) and not icon_val.strip()):
                merged = {**merged, "icon": resolve_icon_by_name(merged.get("name"))}
        _USER_UPSERT_DISPATCH[change.entity_type](
            db,
            user_id=user_id,
            source_change_id=change.change_id,
            payload=merged,
        )


def _parse_payload(raw: Any) -> Optional[dict]:
    """把 ``SyncChange.payload_json`` 归一成 dict。非法 JSON / 非 dict 返回 None。

    DB 里这列声明成 JSON,但 SQLAlchemy 对 SQLite 的 JSON 类型存进去是字符串,
    取出来也可能是字符串;Postgres 直接反序列化成 dict。两种都要 handle。
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, dict):
        return raw
    return None
