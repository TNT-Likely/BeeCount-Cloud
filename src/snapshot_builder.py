"""从 projection 表按需拼装 snapshot dict。

方案 B 里 projection 是权威源,snapshot 不再 runtime 写入。但 mobile 协议
(`/sync/full`)、snapshot_mutator(web write 路径)还吃 snapshot dict 作输入,所以
提供一个按 (ledger_id, max_change_id) 缓存的 builder。

字段 shape 跟原先 mobile push 来的 snapshot 完全对齐 —— mobile 客户端零改动。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    Ledger,
    ReadBudgetProjection,
    ReadCardRewardRuleProjection,
    ReadDebtProjection,
    ReadInstallmentPeriodProjection,
    ReadInstallmentPlanProjection,
    ReadProjectCategoryBudgetProjection,
    ReadProjectProjection,
    ReadRecurringRuleProjection,
    ReadStockTradeProjection,
    ReadTxProjection,
    ReadTxTemplateProjection,
    SyncChange,
    UserAccountProjection,
    UserCategoryProjection,
    UserTagProjection,
)


def _to_iso_utc(dt) -> str | None:
    """Match snapshot_mutator._to_iso8601 output format ——带 +00:00 后缀。
    SQLite 存 DateTime 可能返回 naive datetime,这里补 UTC 再 isoformat。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def build(db: Session, ledger: Ledger) -> dict[str, Any]:
    """从 projection 5 张表 + Ledger 元数据拼装完整 snapshot dict。

    热路径 —— 用 SQL Core 跳过 ORM hydration(ORM 5000 行 ~65ms,Core ~20ms)。
    调用点:`/sync/full`、`_commit_write` 取 prev 快照做 diff、admin debug。
    """
    ledger_id = ledger.id
    user_id = ledger.user_id

    # Items —— SQL Core,按列顺序取 tuple,比 ORM 快 3 倍
    items: list[dict[str, Any]] = []
    tx_stmt = select(
        ReadTxProjection.sync_id,
        ReadTxProjection.tx_type,
        ReadTxProjection.amount,
        ReadTxProjection.happened_at,
        ReadTxProjection.note,
        # 商店(需求 #11,Phase 11),同样必须进 full snapshot,原因同上(漏
        # SELECT 新欄位的既有 bug 模式)。
        ReadTxProjection.merchant,
        ReadTxProjection.category_sync_id,
        ReadTxProjection.category_name,
        ReadTxProjection.category_kind,
        ReadTxProjection.account_sync_id,
        ReadTxProjection.account_name,
        ReadTxProjection.from_account_sync_id,
        ReadTxProjection.from_account_name,
        ReadTxProjection.to_account_sync_id,
        ReadTxProjection.to_account_name,
        ReadTxProjection.tags_csv,
        ReadTxProjection.tag_sync_ids_json,
        ReadTxProjection.attachments_json,
        ReadTxProjection.tx_index,
        ReadTxProjection.created_by_user_id,
        # 交易级多币种(0018):full pull 重建的 item 不带这两列的话,新 App
        # 全量同步后外币折算全部丢失(apply 缺省 nativeAmount=amount 退化 1:1)。
        ReadTxProjection.currency_code,
        ReadTxProjection.native_amount,
        # 跨幣別轉帳(2026-08),同样必须进 full snapshot,原因同上(漏 SELECT
        # 新欄位的既有 bug 模式:全量同步後轉入端外幣折算全部丟失)。
        ReadTxProjection.to_amount,
        # 手續費/折扣(2026-08 使用者需求),同样必须进 full snapshot,原因同上
        # (CLAUDE.md 記過的既有 bug 模式:漏 SELECT 新欄位 → 下一次
        # `_commit_write` 的 diff 把它當「本來就沒有」靜默清空)。
        ReadTxProjection.base_amount,
        ReadTxProjection.fee_amount,
        ReadTxProjection.fee_label,
        ReadTxProjection.discount_amount,
        ReadTxProjection.discount_label,
        # 退款(§2.6)/ 分期(§2.3)反查字段:同样必须进 full snapshot,否则新
        # 设备首次同步 / 重装后这两个关联全丢。
        ReadTxProjection.refund_of_sync_id,
        ReadTxProjection.installment_plan_sync_id,
        # 週期性收支(§2.12.2 Phase 1.5)反查字段 + 单笔编辑标记,同样必须进
        # full snapshot,否则下一次 _commit_write 的 diff 会把这两个字段撤销。
        ReadTxProjection.recurring_rule_sync_id,
        ReadTxProjection.recurring_occurrence_overridden,
        # 拆帳(§2.4):splits_json 是 LWW merge fallback 的权威值,full snapshot
        # 必须带上,否则新设备首次同步 / _commit_write 的下一次 diff 会把
        # 已有 splits 当成"这笔交易本来就没有 splits"而静默清空。
        ReadTxProjection.splits_json,
        # 借還款追蹤(§2.5 Phase 3)反查字段,同样必须进 full snapshot,原因
        # 同 refund_of_sync_id/installment_plan_sync_id。
        ReadTxProjection.debt_sync_id,
        # 專案(Phase 13,docs/PH13_PROJECT_SD.md)反查字段,同样必须进 full
        # snapshot,原因同 debt_sync_id(CLAUDE.md 記過的既有 bug 模式:漏
        # SELECT 新欄位 → 下一次 `_commit_write` 的 diff 把它當「本來就沒有」
        # 靜默清空)。
        ReadTxProjection.project_sync_id,
        # 信用卡紅利回饋(§2.9.5,2026-08-06 改版)使用者勾選字段 —— 之前漏
        # 加进这个 SELECT(既有 bug,2026-08-04 补上):没有它,`_commit_write`
        # 下一次拿 snapshot_builder.build() 当 prev_snapshot 时会看不到这笔
        # 交易已勾选的规则,编辑该交易任何其它字段都可能把 rewardRuleIds
        # 静默撤销,原因同 splits_json 那条注释。
        ReadTxProjection.reward_rule_sync_ids_json,
        # 信用卡紅利回饋自動入帳(§2.9.5.4 補強)反查字段,同样必须进 full
        # snapshot,原因同上。
        ReadTxProjection.reward_source_tx_sync_id,
        # 延後入帳(§2.10 Phase 5),同样必须进 full snapshot,原因同上
        # (CLAUDE.md 記過的既有 bug 模式:漏 SELECT 新欄位 → 下一次
        # `_commit_write` 的 diff 把它當「本來就沒有」靜默清空)。
        ReadTxProjection.deferred_posting_at,
        # 對帳模式(§2.10,2026-08-09 改版),同样必须进 full snapshot,原因
        # 同上(漏 SELECT 新欄位的既有 bug 模式)。
        ReadTxProjection.reconciled_at,
    ).where(ReadTxProjection.ledger_id == ledger_id).order_by(
        ReadTxProjection.happened_at.desc(),
        ReadTxProjection.tx_index.desc(),
    )
    for row in db.execute(tx_stmt).all():
        (sync_id, tx_type, amount, happened_at, note, merchant,
         cat_sid, cat_name, cat_kind,
         acc_sid, acc_name,
         from_sid, from_name,
         to_sid, to_name,
         tags_csv, tag_ids_json, attachments_json,
         tx_index, created_by,
         currency_code, native_amount, to_amount,
         base_amount, fee_amount, fee_label, discount_amount, discount_label,
         refund_of_id, installment_plan_id,
         recurring_rule_id, recurring_occurrence_overridden,
         splits_json, debt_id, project_id,
         reward_rule_ids_json, reward_source_tx_id,
         deferred_posting_at, reconciled_at) = row
        item: dict[str, Any] = {
            "syncId": sync_id,
            "type": tx_type,
            "amount": amount,
            "happenedAt": _to_iso_utc(happened_at),
        }
        if note is not None:
            item["note"] = note
        if merchant is not None:
            item["merchant"] = merchant
        if cat_sid:
            item["categoryId"] = cat_sid
        if cat_name:
            item["categoryName"] = cat_name
        if cat_kind:
            item["categoryKind"] = cat_kind
        if acc_sid:
            item["accountId"] = acc_sid
        if acc_name:
            item["accountName"] = acc_name
        if from_sid:
            item["fromAccountId"] = from_sid
        if from_name:
            item["fromAccountName"] = from_name
        if to_sid:
            item["toAccountId"] = to_sid
        if to_name:
            item["toAccountName"] = to_name
        if tags_csv:
            item["tags"] = tags_csv
        if tag_ids_json:
            try:
                tag_ids = json.loads(tag_ids_json)
                if isinstance(tag_ids, list) and tag_ids:
                    item["tagIds"] = tag_ids
            except json.JSONDecodeError:
                pass
        if attachments_json:
            try:
                atts = json.loads(attachments_json)
                if isinstance(atts, list) and atts:
                    item["attachments"] = atts
            except json.JSONDecodeError:
                pass
        if tx_index:
            item["txIndex"] = tx_index
        if created_by:
            item["createdByUserId"] = created_by
        # NULL(旧数据)不产生 key,payload 保持干净;统计端 COALESCE 兜底。
        if currency_code:
            item["currencyCode"] = currency_code
        if native_amount is not None:
            item["nativeAmount"] = native_amount
        if to_amount is not None:
            item["toAmount"] = to_amount
        if base_amount is not None:
            item["baseAmount"] = base_amount
        if fee_amount is not None:
            item["feeAmount"] = fee_amount
        if fee_label:
            item["feeLabel"] = fee_label
        if discount_amount is not None:
            item["discountAmount"] = discount_amount
        if discount_label:
            item["discountLabel"] = discount_label
        if refund_of_id:
            item["refundOfId"] = refund_of_id
        if installment_plan_id:
            item["installmentPlanId"] = installment_plan_id
        if recurring_rule_id:
            item["recurringRuleId"] = recurring_rule_id
        if recurring_occurrence_overridden:
            item["recurringOccurrenceOverridden"] = True
        if splits_json:
            try:
                splits = json.loads(splits_json)
                if isinstance(splits, list) and splits:
                    item["splits"] = splits
            except json.JSONDecodeError:
                pass
        if debt_id:
            item["debtId"] = debt_id
        if project_id:
            item["projectId"] = project_id
        if reward_rule_ids_json:
            try:
                reward_rule_ids = json.loads(reward_rule_ids_json)
                if isinstance(reward_rule_ids, list) and reward_rule_ids:
                    item["rewardRuleIds"] = reward_rule_ids
            except json.JSONDecodeError:
                pass
        if reward_source_tx_id:
            item["rewardSourceTxId"] = reward_source_tx_id
        if deferred_posting_at is not None:
            item["deferredPostingAt"] = _to_iso_utc(deferred_posting_at)
        if reconciled_at is not None:
            item["reconciledAt"] = _to_iso_utc(reconciled_at)
        items.append(item)

    # Accounts —— user-global per-user 表,按 user_id 取。snapshot 内仍把全用户
     # 的账户都铺出来:mobile 早期版本依赖 snapshot.accounts 完整 — 用户多账本
     # 时数据一致(同一份 accounts 拷贝到每个账本的 snapshot)。
    accounts: list[dict[str, Any]] = []
    acc_stmt = select(
        UserAccountProjection.sync_id,
        UserAccountProjection.name,
        UserAccountProjection.account_type,
        UserAccountProjection.currency,
        UserAccountProjection.initial_balance,
        UserAccountProjection.note,
        UserAccountProjection.credit_limit,
        UserAccountProjection.billing_day,
        UserAccountProjection.payment_due_day,
        UserAccountProjection.bank_name,
        UserAccountProjection.card_last_four,
        UserAccountProjection.parent_account_id,
        UserAccountProjection.hidden,
        UserAccountProjection.auto_pay_enabled,
        UserAccountProjection.auto_pay_from_account_id,
        UserAccountProjection.avatar_cloud_file_id,
        UserAccountProjection.avatar_cloud_sha256,
        UserAccountProjection.swipesmart_card_id,
        UserAccountProjection.include_in_total,
        UserAccountProjection.sort_order,
        UserAccountProjection.investment_settings_json,
    ).where(UserAccountProjection.user_id == user_id)
    for (
        sid,
        name,
        acc_type,
        acc_ccy,
        init_bal,
        note,
        credit_limit,
        billing_day,
        payment_due_day,
        bank_name,
        card_last_four,
        parent_account_id,
        hidden,
        auto_pay_enabled,
        auto_pay_from_account_id,
        avatar_cloud_file_id,
        avatar_cloud_sha256,
        swipesmart_card_id,
        include_in_total,
        sort_order,
        investment_settings_json,
    ) in db.execute(acc_stmt).all():
        acc: dict[str, Any] = {"syncId": sid, "name": name or ""}
        if acc_type:
            acc["type"] = acc_type
        if acc_ccy:
            acc["currency"] = acc_ccy
        if init_bal is not None:
            acc["initialBalance"] = init_bal
        if note:
            acc["note"] = note
        if credit_limit is not None:
            acc["creditLimit"] = credit_limit
        if billing_day is not None:
            acc["billingDay"] = billing_day
        if payment_due_day is not None:
            acc["paymentDueDay"] = payment_due_day
        if bank_name:
            acc["bankName"] = bank_name
        if card_last_four:
            acc["cardLastFour"] = card_last_four
        if parent_account_id:
            acc["parentAccountId"] = parent_account_id
        # 账户隐藏(issue #240):无条件输出(不像其它扩展字段那样"有值才带
        # key"),与 App serializeAccount 无条件发 hidden 对齐,保 /sync/full
        # 重装 / 新设备首次同步时隐藏标记不丢(03-tech-design-cloud.md §二 (B))。
        acc["hidden"] = bool(hidden)
        # 自動扣繳(§2.9,2026-08-04 改版):NOT NULL 布尔列,同 hidden 无条件
        # 输出。2026-08-02 补强发现这两个字段(连同下面头像字段)原本完全没
        # 进这个 SELECT——写路径的 diff-emit 用这个函数重建"prev"基线,任何
        # 跟 autopay/avatar 无关的账户编辑(改名/备注等)都会因为这里漏选,
        # 导致 diff 出的 entity dict 里这些 key 整个缺失,被 upsert_account
        # 当成"没传"写成 null/False,静默清空用户已设置的自動扣繳。
        acc["autoPayEnabled"] = bool(auto_pay_enabled)
        if auto_pay_from_account_id:
            acc["autoPayFromAccountId"] = auto_pay_from_account_id
        # 帳戶頭像(2026-08-02 補強)。
        if avatar_cloud_file_id:
            acc["avatarCloudFileId"] = avatar_cloud_file_id
        if avatar_cloud_sha256:
            acc["avatarCloudSha256"] = avatar_cloud_sha256
        # SwipeSmart 卡片對照(Phase 14)。
        if swipesmart_card_id:
            acc["swipesmartCardId"] = swipesmart_card_id
        # 納入總餘額(Phase 18):NOT NULL 布尔列,同 hidden/autoPayEnabled 无
        # 条件输出,避免这个 SELECT 漏选导致 diff-emit 重建基线时静默清空。
        acc["includeInTotal"] = bool(include_in_total)
        # 帳戶清單拖曳排序(2026-09-05):nullable,同 creditLimit/billingDay
        # 「有值才带 key」的写法——同上面 includeInTotal 注释描述的坑,這個
        # SELECT 漏選会导致 web 写路径 diff-emit 重建 prev 基线时这个 key
        # 整个缺失,被 upsert_account 当成"没传"写成 null,静默清空排序。
        if sort_order is not None:
            acc["sortOrder"] = sort_order
        # 股票持股(2026-09-28):投資理財帳戶費用設定。同 sortOrder「有值才带
        # key」——漏選的話 web 改帳戶名時 diff-emit 重建的基線缺這個 key,
        # upsert_account 會把使用者的費用設定靜默清成 None。
        if investment_settings_json:
            try:
                parsed_settings = json.loads(investment_settings_json)
            except json.JSONDecodeError:
                parsed_settings = None
            if isinstance(parsed_settings, dict):
                acc["investmentSettings"] = parsed_settings
        accounts.append(acc)

    # Categories —— 同 accounts,user-global per-user。
    categories: list[dict[str, Any]] = []
    cat_stmt = select(
        UserCategoryProjection.sync_id,
        UserCategoryProjection.name,
        UserCategoryProjection.kind,
        UserCategoryProjection.level,
        UserCategoryProjection.sort_order,
        UserCategoryProjection.icon,
        UserCategoryProjection.icon_type,
        UserCategoryProjection.custom_icon_path,
        UserCategoryProjection.icon_cloud_file_id,
        UserCategoryProjection.icon_cloud_sha256,
        UserCategoryProjection.parent_name,
        UserCategoryProjection.color,
    ).where(UserCategoryProjection.user_id == user_id).order_by(
        UserCategoryProjection.sort_order.asc(),
        UserCategoryProjection.name.asc(),
    )
    for (sid, name, kind, level, sort_order, icon, icon_type,
         custom_icon, icon_fid, icon_sha, parent, color) in db.execute(cat_stmt).all():
        cat: dict[str, Any] = {"syncId": sid, "name": name or ""}
        if kind:
            cat["kind"] = kind
        if level is not None:
            cat["level"] = level
        if sort_order is not None:
            cat["sortOrder"] = sort_order
        if icon:
            cat["icon"] = icon
        if icon_type:
            cat["iconType"] = icon_type
        if custom_icon:
            cat["customIconPath"] = custom_icon
        if icon_fid:
            cat["iconCloudFileId"] = icon_fid
        if icon_sha:
            cat["iconCloudSha256"] = icon_sha
        if parent:
            cat["parentName"] = parent
        if color:
            cat["color"] = color
        categories.append(cat)

    # Tags —— user-global per-user。
    tags: list[dict[str, Any]] = []
    tag_stmt = select(
        UserTagProjection.sync_id,
        UserTagProjection.name,
        UserTagProjection.color,
    ).where(UserTagProjection.user_id == user_id).order_by(UserTagProjection.name.asc())
    for sid, name, color in db.execute(tag_stmt).all():
        t: dict[str, Any] = {"syncId": sid, "name": name or ""}
        if color:
            t["color"] = color
        tags.append(t)

    # Budgets
    # mobile sync_engine._applyBudgetChange 用 payload['ledgerSyncId'] 解析本地
    # ledger int id(不像 tx 用 change.ledger_id 字段),所以 budget snapshot 必
    # 须显式带这个字段;不带则 mobile 收到 change 后会因 localLedgerId==null 直接
    # skip,web 改了 mobile 那边永远刷不出来。
    budgets: list[dict[str, Any]] = []
    bud_stmt = select(
        ReadBudgetProjection.sync_id,
        ReadBudgetProjection.budget_type,
        ReadBudgetProjection.category_sync_id,
        ReadBudgetProjection.amount,
        ReadBudgetProjection.period,
        ReadBudgetProjection.start_day,
        ReadBudgetProjection.enabled,
    ).where(ReadBudgetProjection.ledger_id == ledger_id)
    for sid, btype, cat_sid, amt, period, start_day, enabled in db.execute(bud_stmt).all():
        b: dict[str, Any] = {"syncId": sid, "ledgerSyncId": ledger.external_id}
        if btype:
            b["type"] = btype
        if cat_sid:
            b["categoryId"] = cat_sid
        if amt is not None:
            b["amount"] = amt
        if period:
            b["period"] = period
        if start_day is not None:
            b["startDay"] = start_day
        b["enabled"] = bool(enabled)
        budgets.append(b)

    # Recurring rules(§2.2)
    recurring_rules: list[dict[str, Any]] = []
    rec_stmt = select(
        ReadRecurringRuleProjection.sync_id,
        ReadRecurringRuleProjection.tx_type,
        ReadRecurringRuleProjection.amount,
        ReadRecurringRuleProjection.note,
        ReadRecurringRuleProjection.category_sync_id,
        ReadRecurringRuleProjection.account_sync_id,
        ReadRecurringRuleProjection.from_account_sync_id,
        ReadRecurringRuleProjection.to_account_sync_id,
        ReadRecurringRuleProjection.merchant,
        ReadRecurringRuleProjection.project_sync_id,
        ReadRecurringRuleProjection.tag_sync_ids_json,
        ReadRecurringRuleProjection.frequency,
        ReadRecurringRuleProjection.interval,
        ReadRecurringRuleProjection.next_run_at,
        ReadRecurringRuleProjection.end_at,
        ReadRecurringRuleProjection.enabled,
        ReadRecurringRuleProjection.generated_until_at,
        ReadRecurringRuleProjection.advanced_rule_json,
        ReadRecurringRuleProjection.base_amount,
        ReadRecurringRuleProjection.fee_amount,
        ReadRecurringRuleProjection.fee_label,
        ReadRecurringRuleProjection.discount_amount,
        ReadRecurringRuleProjection.discount_label,
        ReadRecurringRuleProjection.reward_rule_sync_ids_json,
        # 股票定期定額(2026-09-28,2026-09-29 補):漏選這幾欄的話,Web 端任何
        # 規則 PATCH(snapshot_mutator 在這份快照上改完再 diff、整筆 upsert)
        # 都會把 kind 沖回 'general'。
        ReadRecurringRuleProjection.kind,
        ReadRecurringRuleProjection.market,
        ReadRecurringRuleProjection.symbol,
        ReadRecurringRuleProjection.security_name,
        ReadRecurringRuleProjection.stock_fee_rate,
        ReadRecurringRuleProjection.stock_fee_min,
    ).where(ReadRecurringRuleProjection.ledger_id == ledger_id)
    for (sid, tx_type, amount, note, cat_sid, acc_sid, from_sid, to_sid,
         merchant, project_sid, tag_ids_json,
         frequency, interval, next_run_at, end_at, enabled,
         generated_until_at, advanced_rule_json,
         base_amount, fee_amount, fee_label, discount_amount, discount_label,
         reward_rule_ids_json,
         rule_kind, rule_market, rule_symbol, rule_security_name,
         stock_fee_rate, stock_fee_min) in db.execute(rec_stmt).all():
        r: dict[str, Any] = {
            "syncId": sid,
            "txType": tx_type,
            "amount": amount,
            "frequency": frequency,
            "interval": interval,
            "nextRunAt": _to_iso_utc(next_run_at),
            "enabled": bool(enabled),
        }
        if note is not None:
            r["note"] = note
        if cat_sid:
            r["categoryId"] = cat_sid
        if acc_sid:
            r["accountId"] = acc_sid
        if from_sid:
            r["fromAccountId"] = from_sid
        if to_sid:
            r["toAccountId"] = to_sid
        if merchant is not None:
            r["merchant"] = merchant
        if project_sid:
            r["projectId"] = project_sid
        if tag_ids_json:
            try:
                parsed_tag_ids = json.loads(tag_ids_json)
                if isinstance(parsed_tag_ids, list):
                    r["tagIds"] = [str(v) for v in parsed_tag_ids]
            except json.JSONDecodeError:
                pass
        if end_at is not None:
            r["endAt"] = _to_iso_utc(end_at)
        if generated_until_at is not None:
            r["generatedUntilAt"] = _to_iso_utc(generated_until_at)
        if advanced_rule_json:
            try:
                parsed_rule = json.loads(advanced_rule_json)
                if isinstance(parsed_rule, dict):
                    r["advancedRuleJson"] = parsed_rule
            except json.JSONDecodeError:
                pass
        # 手續費/折扣/信用卡回饋(2026-08 使用者回饋)。
        if base_amount is not None:
            r["baseAmount"] = base_amount
        if fee_amount is not None:
            r["feeAmount"] = fee_amount
        if fee_label is not None:
            r["feeLabel"] = fee_label
        if discount_amount is not None:
            r["discountAmount"] = discount_amount
        if discount_label is not None:
            r["discountLabel"] = discount_label
        if reward_rule_ids_json:
            try:
                parsed_reward_ids = json.loads(reward_rule_ids_json)
                if isinstance(parsed_reward_ids, list):
                    r["rewardRuleIds"] = [str(v) for v in parsed_reward_ids]
            except json.JSONDecodeError:
                pass
        # 股票定期定額:同 snapshot_mutator.create_recurring_rule,kind='general'
        # 不寫 key(維持既有規則的快照形狀不變)。
        if rule_kind and rule_kind != "general":
            r["kind"] = rule_kind
        if rule_market:
            r["market"] = rule_market
        if rule_symbol:
            r["symbol"] = rule_symbol
        if rule_security_name is not None:
            r["securityName"] = rule_security_name
        if stock_fee_rate is not None:
            r["stockFeeRate"] = stock_fee_rate
        if stock_fee_min is not None:
            r["stockFeeMin"] = stock_fee_min
        recurring_rules.append(r)

    # Installment periods(§2.12.1 Phase 1.5)—— 先读,installment plan 的
    # paidPeriods/nextPeriodAt 要从这里 derive(不再信任 projection 里那两个
    # 不再被排程写入的历史相容字段)。
    periods_by_plan: dict[str, list[dict[str, Any]]] = {}
    installment_periods: list[dict[str, Any]] = []
    period_stmt = select(
        ReadInstallmentPeriodProjection.sync_id,
        ReadInstallmentPeriodProjection.plan_sync_id,
        ReadInstallmentPeriodProjection.period_no,
        ReadInstallmentPeriodProjection.due_at,
        ReadInstallmentPeriodProjection.principal_amount,
        ReadInstallmentPeriodProjection.interest_amount,
        ReadInstallmentPeriodProjection.total_amount,
        ReadInstallmentPeriodProjection.status,
        ReadInstallmentPeriodProjection.tx_sync_id,
    ).where(ReadInstallmentPeriodProjection.ledger_id == ledger_id).order_by(
        ReadInstallmentPeriodProjection.plan_sync_id,
        ReadInstallmentPeriodProjection.period_no,
    )
    for (sid, plan_sid, period_no, due_at, principal_amount, interest_amount,
         total_amount, status, tx_sid) in db.execute(period_stmt).all():
        entry: dict[str, Any] = {
            "syncId": sid,
            "planId": plan_sid,
            "periodNo": period_no,
            "dueAt": _to_iso_utc(due_at),
            "principalAmount": principal_amount,
            "interestAmount": interest_amount,
            "totalAmount": total_amount,
            "status": status,
        }
        if tx_sid:
            entry["txId"] = tx_sid
        installment_periods.append(entry)
        periods_by_plan.setdefault(plan_sid, []).append(entry)

    # Installment plans(§2.3 / §2.12.1)
    installment_plans: list[dict[str, Any]] = []
    ins_stmt = select(
        ReadInstallmentPlanProjection.sync_id,
        ReadInstallmentPlanProjection.total_amount,
        ReadInstallmentPlanProjection.periods,
        ReadInstallmentPlanProjection.period_amount,
        ReadInstallmentPlanProjection.first_period_at,
        ReadInstallmentPlanProjection.account_sync_id,
        ReadInstallmentPlanProjection.category_sync_id,
        ReadInstallmentPlanProjection.note,
        ReadInstallmentPlanProjection.status,
        ReadInstallmentPlanProjection.repayment_method,
        ReadInstallmentPlanProjection.interest_period,
        ReadInstallmentPlanProjection.interest_rate,
        ReadInstallmentPlanProjection.round_amounts,
        ReadInstallmentPlanProjection.remainder_position,
        ReadInstallmentPlanProjection.grace_period_months,
        ReadInstallmentPlanProjection.offset_breakdown_json,
    ).where(ReadInstallmentPlanProjection.ledger_id == ledger_id)
    now = datetime.now(timezone.utc)
    for (sid, total_amount, periods, period_amount, first_period_at,
         acc_sid, cat_sid, note, status, repayment_method, interest_period,
         interest_rate, round_amounts, remainder_position,
         grace_period_months, offset_breakdown_json) in db.execute(ins_stmt).all():
        plan_periods = periods_by_plan.get(sid) or []
        # paidPeriods/nextPeriodAt/periodAmount 不再由排程写入,这里从 period
        # 明细即时算出(见 ReadInstallmentPlanProjection docstring)。没有
        # period 明细(理论上不会出现,兜底)时退回旧字段语义:0 期已付,
        # 下一期=首期,periodAmount=创建时算的天真平均值。
        periods_sorted = sorted(
            (datetime.fromisoformat(p["dueAt"]), p["totalAmount"])
            for p in plan_periods if p.get("dueAt")
        )
        due_dates = [d for d, _ in periods_sorted]
        paid_periods = sum(1 for d in due_dates if d <= now)
        future_periods = [(d, amt) for d, amt in periods_sorted if d > now]
        if future_periods:
            next_period_at, current_period_amount = future_periods[0]
        elif periods_sorted:
            next_period_at, current_period_amount = periods_sorted[-1]
        else:
            next_period_at, current_period_amount = first_period_at, period_amount
        p: dict[str, Any] = {
            "syncId": sid,
            "totalAmount": total_amount,
            "periods": periods,
            "periodAmount": current_period_amount,
            "firstPeriodAt": _to_iso_utc(first_period_at),
            "nextPeriodAt": _to_iso_utc(next_period_at),
            "paidPeriods": paid_periods,
            "status": status,
            "repaymentMethod": repayment_method,
            "interestPeriod": interest_period,
            "interestRate": interest_rate,
            "roundAmounts": bool(round_amounts),
            "remainderPosition": remainder_position,
            "gracePeriodMonths": grace_period_months,
        }
        if acc_sid:
            p["accountId"] = acc_sid
        if cat_sid:
            p["categoryId"] = cat_sid
        if note is not None:
            p["note"] = note
        if offset_breakdown_json:
            p["offsetBreakdownJson"] = offset_breakdown_json
        installment_plans.append(p)

    # 借還款追蹤(§2.5 Phase 3)
    debts: list[dict[str, Any]] = []
    debt_stmt = select(
        ReadDebtProjection.sync_id,
        ReadDebtProjection.direction,
        ReadDebtProjection.counterparty_name,
        ReadDebtProjection.principal_amount,
        ReadDebtProjection.due_at,
        ReadDebtProjection.note,
        ReadDebtProjection.closed_at,
        ReadDebtProjection.category_sync_id,
        ReadDebtProjection.origin_tx_sync_id,
        ReadDebtProjection.excluded_from_total,
    ).where(ReadDebtProjection.ledger_id == ledger_id)
    for (
        sid, direction, counterparty_name, principal_amount, due_at, note,
        closed_at, cat_sid, origin_tx_sid, excluded_from_total,
    ) in db.execute(debt_stmt).all():
        d: dict[str, Any] = {
            "syncId": sid,
            "direction": direction,
            "counterpartyName": counterparty_name,
            "principalAmount": principal_amount,
            "excludedFromTotal": bool(excluded_from_total),
        }
        if due_at is not None:
            d["dueAt"] = _to_iso_utc(due_at)
        if note is not None:
            d["note"] = note
        if closed_at is not None:
            d["closedAt"] = _to_iso_utc(closed_at)
        if cat_sid:
            d["categoryId"] = cat_sid
        if origin_tx_sid:
            d["originTxId"] = origin_tx_sid
        debts.append(d)

    # 股票交易明細(2026-09-28,docs/STOCK_HOLDINGS_SD.md)。必須涵蓋
    # ReadStockTradeProjection 全部欄位——upsert_stock_trade 是整列覆蓋,
    # 漏選的欄位在 web 編輯時會被 diff-emit 沖成 NULL。
    stock_trades: list[dict[str, Any]] = []
    stock_stmt = select(
        ReadStockTradeProjection.sync_id,
        ReadStockTradeProjection.account_sync_id,
        ReadStockTradeProjection.market,
        ReadStockTradeProjection.symbol,
        ReadStockTradeProjection.security_name,
        ReadStockTradeProjection.trade_type,
        ReadStockTradeProjection.shares,
        ReadStockTradeProjection.price,
        ReadStockTradeProjection.fee,
        ReadStockTradeProjection.tax,
        ReadStockTradeProjection.amount,
        ReadStockTradeProjection.currency,
        ReadStockTradeProjection.trade_date,
        ReadStockTradeProjection.tx_sync_id,
        ReadStockTradeProjection.dividend_event_ref,
        ReadStockTradeProjection.note,
        ReadStockTradeProjection.created_by_user_id,
    ).where(ReadStockTradeProjection.ledger_id == ledger_id)
    for (
        sid, account_sid, market, symbol, security_name, trade_type, shares,
        price, fee, tax, amount, currency, trade_date, tx_sid,
        dividend_event_ref, note, created_by,
    ) in db.execute(stock_stmt).all():
        t: dict[str, Any] = {
            "syncId": sid,
            "market": market,
            "symbol": symbol,
            "tradeType": trade_type,
            "shares": shares,
            "fee": fee,
            "tax": tax,
            "amount": amount,
        }
        if account_sid:
            t["accountId"] = account_sid
        if security_name is not None:
            t["securityName"] = security_name
        if price is not None:
            t["price"] = price
        if currency:
            t["currency"] = currency
        if trade_date is not None:
            t["tradeDate"] = _to_iso_utc(trade_date)
        if tx_sid:
            t["txId"] = tx_sid
        if dividend_event_ref:
            t["dividendEventRef"] = dividend_event_ref
        if note is not None:
            t["note"] = note
        if created_by:
            t["createdByUserId"] = created_by
        stock_trades.append(t)

    # 專案(Phase 13,docs/PH13_PROJECT_SD.md)
    projects: list[dict[str, Any]] = []
    project_stmt = select(
        ReadProjectProjection.sync_id,
        ReadProjectProjection.name,
        ReadProjectProjection.icon,
        ReadProjectProjection.budget_amount,
        ReadProjectProjection.period_type,
        ReadProjectProjection.period_start,
        ReadProjectProjection.period_end,
        ReadProjectProjection.carryover_enabled,
        ReadProjectProjection.visible_on_home,
        ReadProjectProjection.enabled,
        ReadProjectProjection.sort_order,
        ReadProjectProjection.income_included_in_budget,
        ReadProjectProjection.daily_budget_enabled,
        ReadProjectProjection.daily_budget_mode,
        ReadProjectProjection.reminder_threshold_percent,
    ).where(ReadProjectProjection.ledger_id == ledger_id)
    for (
        sid, name, icon, budget_amount, period_type, period_start, period_end,
        carryover_enabled, visible_on_home, enabled, sort_order,
        income_included_in_budget, daily_budget_enabled, daily_budget_mode,
        reminder_threshold_percent,
    ) in db.execute(project_stmt).all():
        p: dict[str, Any] = {
            "syncId": sid,
            "name": name,
            "periodType": period_type or "monthly",
            "carryoverEnabled": bool(carryover_enabled),
            "visibleOnHome": bool(visible_on_home),
            "enabled": bool(enabled),
            "sortOrder": sort_order or 0,
            "incomeIncludedInBudget": bool(income_included_in_budget),
            "dailyBudgetEnabled": bool(daily_budget_enabled),
        }
        if icon is not None:
            p["icon"] = icon
        if budget_amount is not None:
            p["budgetAmount"] = budget_amount
        if period_start is not None:
            p["periodStart"] = period_start.isoformat()
        if period_end is not None:
            p["periodEnd"] = period_end.isoformat()
        if daily_budget_mode is not None:
            p["dailyBudgetMode"] = daily_budget_mode
        if reminder_threshold_percent is not None:
            p["reminderThresholdPercent"] = reminder_threshold_percent
        projects.append(p)

    # 專案分類子預算(docs/2026-09-06-project-category-budget-period-switch-
    # design.md §2.2/§7.2)
    project_category_budgets: list[dict[str, Any]] = []
    pcb_stmt = select(
        ReadProjectCategoryBudgetProjection.sync_id,
        ReadProjectCategoryBudgetProjection.project_sync_id,
        ReadProjectCategoryBudgetProjection.category_sync_id,
        ReadProjectCategoryBudgetProjection.mode,
        ReadProjectCategoryBudgetProjection.fixed_amount,
        ReadProjectCategoryBudgetProjection.percentage,
        ReadProjectCategoryBudgetProjection.carryover_enabled,
        ReadProjectCategoryBudgetProjection.sort_order,
    ).where(ReadProjectCategoryBudgetProjection.ledger_id == ledger_id)
    for (
        sid, project_sid, category_sid, mode, fixed_amount, percentage,
        carryover_enabled, sort_order,
    ) in db.execute(pcb_stmt).all():
        pcb: dict[str, Any] = {
            "syncId": sid,
            "projectId": project_sid,
            "categoryId": category_sid,
            "mode": mode or "fixed",
            "carryoverEnabled": bool(carryover_enabled),
            "sortOrder": sort_order or 0,
        }
        if fixed_amount is not None:
            pcb["fixedAmount"] = fixed_amount
        if percentage is not None:
            pcb["percentage"] = percentage
        project_category_budgets.append(pcb)

    # 交易範本(§2.7 Phase 3)
    tx_templates: list[dict[str, Any]] = []
    tpl_stmt = select(
        ReadTxTemplateProjection.sync_id,
        ReadTxTemplateProjection.name,
        ReadTxTemplateProjection.tx_type,
        ReadTxTemplateProjection.amount,
        ReadTxTemplateProjection.note,
        ReadTxTemplateProjection.category_sync_id,
        ReadTxTemplateProjection.account_sync_id,
        ReadTxTemplateProjection.from_account_sync_id,
        ReadTxTemplateProjection.to_account_sync_id,
        ReadTxTemplateProjection.tag_sync_ids_json,
        ReadTxTemplateProjection.sort_order,
    ).where(ReadTxTemplateProjection.ledger_id == ledger_id).order_by(
        ReadTxTemplateProjection.sort_order.asc(),
    )
    for (sid, name, tx_type, amount, note, cat_sid, acc_sid, from_sid, to_sid,
         tag_ids_json, sort_order) in db.execute(tpl_stmt).all():
        tpl: dict[str, Any] = {
            "syncId": sid,
            "name": name,
            "txType": tx_type,
            "amount": amount,
            "sortOrder": sort_order,
        }
        if note is not None:
            tpl["note"] = note
        if cat_sid:
            tpl["categoryId"] = cat_sid
        if acc_sid:
            tpl["accountId"] = acc_sid
        if from_sid:
            tpl["fromAccountId"] = from_sid
        if to_sid:
            tpl["toAccountId"] = to_sid
        if tag_ids_json:
            try:
                tag_ids = json.loads(tag_ids_json)
                if isinstance(tag_ids, list) and tag_ids:
                    tpl["tagIds"] = tag_ids
            except json.JSONDecodeError:
                pass
        tx_templates.append(tpl)

    # 信用卡紅利回饋規則(§2.9.5 Phase 4.5)—— user-global,同 accounts 按
    # user_id 取,不按 ledger_id(即使 diff/write 引擎走 per-ledger snapshot,
    # 底层这份表跟账本无关)。
    card_reward_rules: list[dict[str, Any]] = []
    crr_stmt = select(
        ReadCardRewardRuleProjection.sync_id,
        ReadCardRewardRuleProjection.account_sync_id,
        ReadCardRewardRuleProjection.label,
        ReadCardRewardRuleProjection.category_sync_ids_json,
        ReadCardRewardRuleProjection.rate_type,
        ReadCardRewardRuleProjection.rate_value,
        ReadCardRewardRuleProjection.rounding,
        ReadCardRewardRuleProjection.total_rounding,
        ReadCardRewardRuleProjection.calc_basis,
        ReadCardRewardRuleProjection.interval,
        ReadCardRewardRuleProjection.min_spend_threshold,
        ReadCardRewardRuleProjection.min_tx_amount,
        ReadCardRewardRuleProjection.cap_amount,
        ReadCardRewardRuleProjection.cap_shared_key,
        ReadCardRewardRuleProjection.starts_at,
        ReadCardRewardRuleProjection.ends_at,
        ReadCardRewardRuleProjection.settlement_type,
        ReadCardRewardRuleProjection.settlement_days,
        ReadCardRewardRuleProjection.settlement_month_offset,
        ReadCardRewardRuleProjection.settlement_day_of_month,
        ReadCardRewardRuleProjection.reward_account_id,
        ReadCardRewardRuleProjection.note,
        ReadCardRewardRuleProjection.enabled,
    ).where(ReadCardRewardRuleProjection.user_id == user_id)
    for (
        sid, acc_sid, label, category_ids_json, rate_type, rate_value, rounding,
        total_rounding, calc_basis, interval, min_spend_threshold, min_tx_amount, cap_amount,
        cap_shared_key, starts_at, ends_at, settlement_type, settlement_days,
        settlement_month_offset, settlement_day_of_month,
        reward_account_id, note, enabled,
    ) in db.execute(crr_stmt).all():
        rule: dict[str, Any] = {
            "syncId": sid,
            "accountId": acc_sid or "",
            "label": label or "",
            "rateType": rate_type or "percentage",
            "rateValue": rate_value,
            "rounding": rounding or "round",
            "totalRounding": total_rounding or "round",
            "calcBasis": calc_basis or "transaction_date",
            "interval": interval or "billing_cycle",
            "settlementType": settlement_type or "manual",
            "enabled": bool(enabled),
        }
        if settlement_days is not None:
            rule["settlementDays"] = settlement_days
        if settlement_month_offset is not None:
            rule["settlementMonthOffset"] = settlement_month_offset
        if settlement_day_of_month is not None:
            rule["settlementDayOfMonth"] = settlement_day_of_month
        if reward_account_id:
            rule["rewardAccountId"] = reward_account_id
        if category_ids_json:
            try:
                category_ids = json.loads(category_ids_json)
                if isinstance(category_ids, list) and category_ids:
                    rule["categoryIds"] = category_ids
            except json.JSONDecodeError:
                pass
        if min_spend_threshold is not None:
            rule["minSpendThreshold"] = min_spend_threshold
        if min_tx_amount is not None:
            rule["minTxAmount"] = min_tx_amount
        if cap_amount is not None:
            rule["capAmount"] = cap_amount
        if cap_shared_key:
            rule["capSharedKey"] = cap_shared_key
        if starts_at is not None:
            rule["startsAt"] = _to_iso_utc(starts_at)
        if ends_at is not None:
            rule["endsAt"] = _to_iso_utc(ends_at)
        if note is not None:
            rule["note"] = note
        card_reward_rules.append(rule)

    return {
        # ledgerSyncId 给 mutator 用 —— 新建预算时要把它写进 budget payload,
        # 让 mobile sync_engine._applyBudgetChange 能解析本地 ledger int id。
        "ledgerSyncId": ledger.external_id,
        "ledgerName": ledger.name or ledger.external_id,
        "currency": ledger.currency or "CNY",
        "monthStartDay": ledger.month_start_day or 1,
        "count": len(items),
        "items": items,
        "accounts": accounts,
        "categories": categories,
        "tags": tags,
        "budgets": budgets,
        "recurringRules": recurring_rules,
        "installmentPlans": installment_plans,
        "installmentPeriods": installment_periods,
        "debts": debts,
        "projects": projects,
        "projectCategoryBudgets": project_category_budgets,
        "txTemplates": tx_templates,
        "cardRewardRules": card_reward_rules,
        "stockTrades": stock_trades,
    }


def latest_change_id(db: Session, ledger_id: str) -> int:
    """Ledger 的 latest change_id(任意 entity_type),当作"当前版本号"。"""
    return int(
        db.scalar(
            select(func.max(SyncChange.change_id)).where(SyncChange.ledger_id == ledger_id)
        )
        or 0
    )
