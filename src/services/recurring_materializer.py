"""週期性收支(§2.2 / Phase 1.5 修正版 §2.12.2)到期物化(MOZE_FEATURE_GAP_SD.md)。

**Phase 1.5 重构说明**:Phase 1 版本这里有两个函式
(`materialize_due_recurring_rules` 逐期扫描 + `materialize_due_installment_plans`
逐期推进分期),每 15 分钟跑一次。§2.12 对照 Moze 原文重新设计后:

- **分期付款**不再需要任何排程 —— 建计画/rebalance/早偿/提前结清都在
  `routers/write/installment_plans.py` 的写入口当下一次算完全部期数,
  `materialize_due_installment_plans` 已整段删除。
- **週期性收支**改成"視窗續產生"语意:建规则时(`routers/write/
  recurring_rules.py` POST 或 `transactions.py` 的 inline `recurring` 分支)
  已经依 `services.recurring_schedule.plan_initial_generation` 批次生成过
  一个视窗(有 `end_at` 全部生成;没有则生成默认窗口),这个模块只负责给
  "没有 end_at 的长期规则"低频(main.py 改成每天一次,不再是 15 分钟)
  续产生下一段视窗,`refill_recurring_windows` 取代原来的
  `materialize_due_recurring_rules`。

**自動扣繳(tx_type=="transfer",2026-08-02 补)是上面这条规则的例外**:
使用者反馈"自動扣繳应该是到期时才检查来源帐户余额够不够,不够就跳过",
但"批次提前生成"这个设计本身就跟"到期才检查余额"矛盾——提前好几个月
生成时,来源帐户到时候的余额根本无从得知。所以 `tx_type=="transfer"` 的
规则完全不吃上面的批次视窗逻辑(`refill_recurring_windows` 的查询显式排除
它们),改用 `materialize_due_transfer_rules`:到期当下才逐笔生成 + 检查
`from_account_id` 当下的记账余额,不够就跳过 + 发通知(去重,同一期不会
重复通知),下次 15 分钟 loop 再重试同一笔;一般收支类规则(expense/
income)不受影响,因为"余额"这个概念对它们本来就不适用。

到期交易怎么写,还是跟 mobile push / web write 生成交易走同一份
`projection.upsert_tx`,只是 SyncChange 的 `updated_by_device_id` 固定成
`_MATERIALIZER_DEVICE_ID`,方便 admin 日志/debug 时区分"这笔交易是排程自动
生成的,不是某台设备推的"。

调用入口:
  - `materialize_all_due(db)`:一次性跑完当前所有需要续产生的规则(含
    `materialize_due_transfer_rules`)并自己 commit。给
    `POST /internal/tasks/materialize-recurring`(见
    src/routers/internal_tasks.py)用;main.py 的周期性 asyncio loop 里,
    `refill_recurring_windows`(非 transfer)走 24 小时低频 loop,
    `materialize_due_transfer_rules`(transfer)另外挂在 debt/card reminder
    同一个 15 分钟 loop 上(时效性要求跟提醒类似,不能等 24 小时)。
  - `refill_recurring_windows` / `materialize_due_transfer_rules` 单独暴露
    给测试直接调用,不 commit(方便测试自己控制事务边界 + 断言)。

**不**做 WS 广播:这是背景任务,不挂在任何 HTTP request / websocket 连接
上,没有天然的"打给谁"的上下文。跟 SYNC_ARCHITECTURE.md §5 描述一致,
mobile/web 各自的轮询兜底会在下一次 poll 捞到这批新交易,不需要这里额外
补一次推送。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import projection
from ..concurrency import lock_ledger_for_materialize
from ..models import (
    Notification,
    ReadRecurringRuleProjection,
    ReadStockTradeProjection,
    ReadTxProjection,
    SecurityQuote,
    SyncChange,
    UserAccountProjection,
)
from ..snapshot_mutator import add_months, stock_trade_amount
from . import notifications as notification_service
from . import recurring_schedule
from .securities import markets, trade_fees
from .securities import store as securities_store

logger = logging.getLogger(__name__)

_MATERIALIZER_DEVICE_ID = "server-recurring-materializer"


def new_sync_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _ensure_aware(dt: datetime) -> datetime:
    """SQLite 存 `DateTime(timezone=True)` 但驱动读回来是 naive —— 跟 `now`
    (tz-aware)比较前先补 UTC 标记,不然 `<=` 直接 TypeError。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def emit_tx(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    now: datetime,
    item: dict[str, Any],
) -> str:
    """写一条 transaction SyncChange + 同事务 projection.upsert_tx。返回新 tx 的 sync_id。"""
    tx_sync_id = str(item["syncId"])
    change_row = SyncChange(
        user_id=user_id,
        ledger_id=ledger_id,
        scope="ledger",
        entity_type="transaction",
        entity_sync_id=tx_sync_id,
        action="upsert",
        payload_json=item,
        updated_at=now,
        updated_by_device_id=_MATERIALIZER_DEVICE_ID,
        updated_by_user_id=user_id,
    )
    db.add(change_row)
    db.flush()
    projection.upsert_tx(
        db, ledger_id=ledger_id, user_id=user_id,
        source_change_id=change_row.change_id, payload=item,
    )
    return tx_sync_id


def _emit_recurring_rule_update(
    db: Session, *, rule: ReadRecurringRuleProjection, now: datetime,
) -> None:
    """把 rule 当前(已推进 generated_until_at / enabled)状态写一条
    SyncChange,保证 mobile/web pull 能看到规则被系统更新过(不然本地缓存的
    generated_until_at 永远停在旧值)。

    **必须涵蓋 `ReadRecurringRuleProjection` 目前全部欄位**(2026-08 使用者
    回饋踩到的教訓):`projection.upsert_recurring_rule` 走 `_upsert` 是
    「conflict 就整列覆蓋」語意,不是 partial merge——這個 payload 漏掉的
    任何欄位都會被這次 upsert 靜默沖成 NULL(曾經漏了 merchant/projectId/
    tagIds,導致每次視窗續產生後這三個欄位就被清空)。"""
    reward_rule_ids: list[Any] | None = None
    if rule.reward_rule_sync_ids_json:
        try:
            parsed = json.loads(rule.reward_rule_sync_ids_json)
            if isinstance(parsed, list):
                reward_rule_ids = parsed
        except json.JSONDecodeError:
            reward_rule_ids = None
    tag_ids: list[Any] | None = None
    if rule.tag_sync_ids_json:
        try:
            parsed_tags = json.loads(rule.tag_sync_ids_json)
            if isinstance(parsed_tags, list):
                tag_ids = parsed_tags
        except json.JSONDecodeError:
            tag_ids = None
    payload = {
        "syncId": rule.sync_id,
        "txType": rule.tx_type,
        "amount": rule.amount,
        "note": rule.note,
        "categoryId": rule.category_sync_id,
        "accountId": rule.account_sync_id,
        "fromAccountId": rule.from_account_sync_id,
        "toAccountId": rule.to_account_sync_id,
        "merchant": rule.merchant,
        "projectId": rule.project_sync_id,
        "tagIds": tag_ids,
        "frequency": rule.frequency,
        "interval": rule.interval,
        "nextRunAt": rule.next_run_at.isoformat(),
        "endAt": rule.end_at.isoformat() if rule.end_at else None,
        "enabled": rule.enabled,
        "generatedUntilAt": rule.generated_until_at.isoformat() if rule.generated_until_at else None,
        "advancedRuleJson": rule.advanced_rule_json,
        "baseAmount": rule.base_amount,
        "feeAmount": rule.fee_amount,
        "feeLabel": rule.fee_label,
        "discountAmount": rule.discount_amount,
        "discountLabel": rule.discount_label,
        "rewardRuleIds": reward_rule_ids,
        # 股票定期定額(2026-09-28):kind 缺省一律補 'general'——即使一般規則
        # 走這裡也要帶上,不能省略(這欄位在 DB 是 NOT NULL,省略等於漏欄位,
        # 犯本函式 docstring 說的那個教訓)。
        "kind": rule.kind or "general",
        "market": rule.market,
        "symbol": rule.symbol,
        "securityName": rule.security_name,
        "stockFeeRate": rule.stock_fee_rate,
        "stockFeeMin": rule.stock_fee_min,
    }
    change_row = SyncChange(
        user_id=rule.user_id,
        ledger_id=rule.ledger_id,
        scope="ledger",
        entity_type="recurring_rule",
        entity_sync_id=rule.sync_id,
        action="upsert",
        payload_json=payload,
        updated_at=now,
        updated_by_device_id=_MATERIALIZER_DEVICE_ID,
        updated_by_user_id=rule.user_id,
    )
    db.add(change_row)
    db.flush()
    projection.upsert_recurring_rule(
        db, ledger_id=rule.ledger_id, user_id=rule.user_id,
        source_change_id=change_row.change_id, payload=payload,
    )


def refill_recurring_windows(db: Session, *, now: datetime | None = None) -> int:
    """低频(main.py 每天一次)"視窗續產生":扫 `enabled=True` 且
    `generated_until_at` 距今 < `recurring_schedule.REFILL_THRESHOLD_DAYS`
    天(或从未设置过,理论上不会出现)的规则,往前补
    `recurring_schedule.REFILL_WINDOW_MONTHS` 个月并推进
    `generated_until_at`。有 `end_at` 且已经生成到 `end_at` 的规则视为"完全
    生成",直接跳过(正常情况下这种规则在生成完成的当下就已经被建规则的
    write endpoint 标记 `enabled=False` 了,这里的检查只是兜底防御)。
    返回生成的交易笔数。不 commit —— 调用方决定事务边界。"""
    now = now or datetime.now(timezone.utc)
    threshold = now + timedelta(days=recurring_schedule.REFILL_THRESHOLD_DAYS)
    rules = db.scalars(
        select(ReadRecurringRuleProjection).where(
            ReadRecurringRuleProjection.enabled.is_(True),
            # 自動扣繳(tx_type=="transfer",2026-08-02 补):这类规则改成
            # materialize_due_transfer_rules 到期才逐笔生成 + 查余额,不再
            # 走这里的"提前批次续窗"——排除掉,避免被两边重复生成。
            ReadRecurringRuleProjection.tx_type != "transfer",
            or_(
                ReadRecurringRuleProjection.generated_until_at.is_(None),
                ReadRecurringRuleProjection.generated_until_at <= threshold,
            ),
        )
    ).all()

    generated = 0
    for rule in rules:
        lock_ledger_for_materialize(db, rule.ledger_id)
        rule.next_run_at = _ensure_aware(rule.next_run_at)
        if rule.end_at is not None:
            rule.end_at = _ensure_aware(rule.end_at)
        generated_until = (
            _ensure_aware(rule.generated_until_at) if rule.generated_until_at else rule.next_run_at
        )
        if rule.end_at is not None and generated_until >= rule.end_at:
            continue

        advanced_rule: dict[str, Any] | None = None
        if rule.advanced_rule_json:
            try:
                advanced_rule = json.loads(rule.advanced_rule_json)
            except json.JSONDecodeError:
                advanced_rule = None

        # generated_until 本身是"上一次已生成的最后一笔",找它之后的下一笔
        # 作为这次续产生的起点(max_count=2:第一笔一定是 generated_until
        # 自己,第二笔才是真正要生成的)。
        following = recurring_schedule.enumerate_occurrences(
            start=generated_until, end=None, frequency=rule.frequency,
            interval=rule.interval, advanced_rule=advanced_rule, max_count=2,
        )
        next_start = following[1] if len(following) > 1 else None
        if next_start is None:
            continue

        if rule.end_at is not None and next_start > rule.end_at:
            rule.generated_until_at = rule.end_at
            rule.enabled = False
            _emit_recurring_rule_update(db, rule=rule, now=now)
            continue

        window_end = add_months(next_start, recurring_schedule.REFILL_WINDOW_MONTHS)
        if rule.end_at is not None and window_end > rule.end_at:
            window_end = rule.end_at
        occurrences = recurring_schedule.enumerate_occurrences(
            start=next_start, end=window_end, frequency=rule.frequency,
            interval=rule.interval, advanced_rule=advanced_rule,
            max_count=recurring_schedule.MAX_OCCURRENCES_PER_GENERATION,
        )
        for occurrence_at in occurrences:
            tx_sync_id = new_sync_id("tx")
            item: dict[str, Any] = {
                "syncId": tx_sync_id,
                "type": rule.tx_type,
                "amount": rule.amount,
                "happenedAt": occurrence_at.isoformat(),
                "recurringRuleId": rule.sync_id,
                "createdByUserId": rule.user_id,
                "updatedByUserId": rule.user_id,
            }
            if rule.note:
                item["note"] = rule.note
            if rule.category_sync_id:
                item["categoryId"] = rule.category_sync_id
            if rule.account_sync_id:
                item["accountId"] = rule.account_sync_id
            if rule.from_account_sync_id:
                item["fromAccountId"] = rule.from_account_sync_id
            if rule.to_account_sync_id:
                item["toAccountId"] = rule.to_account_sync_id
            # 商家/專案/標籤(Phase 24)——原本只在 transactions.py/
            # recurring_rules.py 的「建立當下」occurrence 迴圈有轉發,這個
            # 「視窗續產生」路徑一直漏掉,導致沒有 end_at 的長期規則往後每一
            # 期都不帶商家/專案/標籤(2026-08 使用者回饋 fix by-product)。
            if rule.merchant:
                item["merchant"] = rule.merchant
            if rule.project_sync_id:
                item["projectId"] = rule.project_sync_id
            if rule.tag_sync_ids_json:
                try:
                    parsed_tags = json.loads(rule.tag_sync_ids_json)
                    if isinstance(parsed_tags, list):
                        item["tagIds"] = parsed_tags
                except json.JSONDecodeError:
                    pass
            # 手續費/折扣/信用卡回饋(2026-08 使用者回饋):規則固定屬性,每一
            # 期自動產生的 occurrence 都要繼承。
            if rule.base_amount is not None:
                item["baseAmount"] = rule.base_amount
            if rule.fee_amount is not None:
                item["feeAmount"] = rule.fee_amount
            if rule.fee_label:
                item["feeLabel"] = rule.fee_label
            if rule.discount_amount is not None:
                item["discountAmount"] = rule.discount_amount
            if rule.discount_label:
                item["discountLabel"] = rule.discount_label
            if rule.reward_rule_sync_ids_json:
                try:
                    parsed_rewards = json.loads(rule.reward_rule_sync_ids_json)
                    if isinstance(parsed_rewards, list):
                        item["rewardRuleIds"] = parsed_rewards
                except json.JSONDecodeError:
                    pass
            emit_tx(db, ledger_id=rule.ledger_id, user_id=rule.user_id, now=now, item=item)
            generated += 1

        if occurrences:
            rule.generated_until_at = occurrences[-1]
        if rule.end_at is not None and rule.generated_until_at is not None and rule.generated_until_at >= rule.end_at:
            rule.enabled = False

        if occurrences:
            notification_service.create_notification(
                db,
                user_id=rule.user_id,
                category="reminder",
                title="週期性收支已续期",
                body=(rule.note or "") or f"一条週期性收支规则已自动续产生 {len(occurrences)} 笔未来交易",
                payload={
                    "ledgerId": notification_service.resolve_ledger_external_id(db, rule.ledger_id),
                    "recurringRuleId": rule.sync_id,
                },
            )
        _emit_recurring_rule_update(db, rule=rule, now=now)

    if generated:
        logger.info("recurring_materializer: refilled %d transactions", generated)
    return generated


def compute_account_balance(
    db: Session, *, user_id: str, account_sync_id: str, now: datetime | None = None,
) -> float:
    """当下记账余额 = initial_balance + income - expense - transfer_out +
    transfer_in + adjustment,跟 `routers/read/workspace.py::
    list_workspace_accounts` 算单个帐户余额的公式完全一致(那边是批量算
    全部帐户,这里只算一个)。用来给 `materialize_due_transfer_rules` 判断
    "来源帐户当下够不够扣",也给 `write/accounts.py::balance_adjustment_ep`
    (§2.10 Phase 5)算「目标余额 - 当下余额」的差额。adjustment(§2.10)
    的 `amount` 本身就是带正负号的差量,直接加总(不像 income/expense 分开
    两个 CASE 再相减)。

    2026-09-29 對齊修正:以前這裡(1)沒排除 `happened_at > now` 的未來交易
    ——一般收支週期規則建立當下會預生成未來 12 個月的 occurrence,交割戶只要
    掛了任何一條每月支出規則,這裡就會把一整年的未來支出都扣掉,股票定期定額/
    自動扣繳被誤判成餘額不足;(2)轉帳沒算轉出側手續費(`fee_amount`)/轉入側
    折損(`discount_amount`)。兩者都跟 Web 顯示的帳戶餘額不一致,現在照
    `list_workspace_accounts` 同一套條件。"""
    now = now or datetime.now(timezone.utc)
    account = db.scalar(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.sync_id == account_sync_id,
        )
    )
    init_bal = float(account.initial_balance or 0.0) if account else 0.0
    not_future = ReadTxProjection.happened_at <= now
    income = float(db.scalar(
        select(func.coalesce(func.sum(ReadTxProjection.amount), 0.0)).where(
            ReadTxProjection.account_sync_id == account_sync_id, ReadTxProjection.tx_type == "income",
            not_future,
        )
    ) or 0.0)
    expense = float(db.scalar(
        select(func.coalesce(func.sum(ReadTxProjection.amount), 0.0)).where(
            ReadTxProjection.account_sync_id == account_sync_id, ReadTxProjection.tx_type == "expense",
            not_future,
        )
    ) or 0.0)
    transfer_out = float(db.scalar(
        select(
            func.coalesce(
                func.sum(ReadTxProjection.amount + func.coalesce(ReadTxProjection.fee_amount, 0.0)), 0.0
            )
        ).where(
            ReadTxProjection.from_account_sync_id == account_sync_id, ReadTxProjection.tx_type == "transfer",
            not_future,
        )
    ) or 0.0)
    # 跨幣別轉帳(2026-08):轉入端要用轉入帳戶自身幣別的金額,不是轉出端的
    # amount——同幣種轉帳 to_amount 是 NULL,COALESCE 回退 amount,行為不變。
    transfer_in = float(db.scalar(
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(ReadTxProjection.to_amount, ReadTxProjection.amount)
                    - func.coalesce(ReadTxProjection.discount_amount, 0.0)
                ),
                0.0,
            )
        ).where(
            ReadTxProjection.to_account_sync_id == account_sync_id, ReadTxProjection.tx_type == "transfer",
            not_future,
        )
    ) or 0.0)
    adjustment = float(db.scalar(
        select(func.coalesce(func.sum(ReadTxProjection.amount), 0.0)).where(
            ReadTxProjection.account_sync_id == account_sync_id, ReadTxProjection.tx_type == "adjustment",
            not_future,
        )
    ) or 0.0)
    return init_bal + income - expense - transfer_out + transfer_in + adjustment


def _already_notified_insufficient(
    db: Session, *, user_id: str, rule_id: str, occurrence_iso: str,
) -> bool:
    """同一条規則的同一期(occurrence_iso)只通知一次,不然每 15 分鐘 loop
    重試一次就會重複發通知——跟 `credit_card_reminders._already_sent` 同款
    去重模式。"""
    rows = db.scalars(
        select(Notification.payload_json).where(
            Notification.user_id == user_id,
            Notification.category == "reminder",
        )
    ).all()
    for payload in rows:
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("recurringRuleId") == rule_id
            and payload.get("occurrenceAt") == occurrence_iso
            and payload.get("kind") == "insufficient_funds"
        ):
            return True
    return False


def materialize_due_transfer_rules(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """自動扣繳(tx_type=="transfer")規則:到期當下才逐筆生成,生成前先查
    `from_account_id` 當下的記帳餘額——不夠就跳過(不推進
    `generated_until_at`,下次 loop 重試同一筆並通知使用者),夠就正常生成
    並推進。跟 `refill_recurring_windows` 分工:那個函式的查詢已經排除
    `tx_type=="transfer"`,兩者不會重複處理同一條規則。

    每條規則每次呼叫最多追上到 `now` 為止的所有到期筆數(理論上多數情況下
    只有 0~1 筆,除非 15 分鐘 loop 曾經中斷很久)。碰到餘額不足就停止繼續
    往後追(不追未來筆,避免"這期沒過、下一期直接跳過去生成"的錯亂)。
    不 commit —— 調用方決定事務邊界。返回 {"materialized": N,
    "skipped_insufficient": M}。"""
    now = now or datetime.now(timezone.utc)
    rules = db.scalars(
        select(ReadRecurringRuleProjection).where(
            ReadRecurringRuleProjection.enabled.is_(True),
            ReadRecurringRuleProjection.tx_type == "transfer",
            # 股票定期定額(2026-09-28):這類規則雖然也是 tx_type='transfer',
            # 但要生成 stock_trade(含股數/手續費試算),改走
            # materialize_due_stock_rules,這裡要排除掉,不然會被當成普通
            # 自動扣繳生成一筆沒有 stock_trade 明細的裸轉帳。
            ReadRecurringRuleProjection.kind != "stock_dca",
        )
    ).all()

    materialized = 0
    skipped = 0
    for rule in rules:
        if not rule.from_account_sync_id:
            continue  # 資料異常防禦:transfer 規則理論上一定有來源帳戶
        lock_ledger_for_materialize(db, rule.ledger_id)
        rule.next_run_at = _ensure_aware(rule.next_run_at)
        if rule.end_at is not None:
            rule.end_at = _ensure_aware(rule.end_at)

        advanced_rule: dict[str, Any] | None = None
        if rule.advanced_rule_json:
            try:
                advanced_rule = json.loads(rule.advanced_rule_json)
            except json.JSONDecodeError:
                advanced_rule = None

        rule_changed = False
        while True:
            generated_until = _ensure_aware(rule.generated_until_at) if rule.generated_until_at else None
            if generated_until is None:
                next_occurrence: datetime | None = rule.next_run_at
            else:
                following = recurring_schedule.enumerate_occurrences(
                    start=generated_until, end=None, frequency=rule.frequency,
                    interval=rule.interval, advanced_rule=advanced_rule, max_count=2,
                )
                next_occurrence = following[1] if len(following) > 1 else None
            if next_occurrence is None or next_occurrence > now:
                break  # 还没到期(或没有下一期),下次 loop 再看

            if rule.end_at is not None and next_occurrence > rule.end_at:
                rule.generated_until_at = rule.end_at
                rule.enabled = False
                rule_changed = True
                break

            balance = compute_account_balance(
                db, user_id=rule.user_id, account_sync_id=rule.from_account_sync_id, now=now,
            )
            if balance < rule.amount - 1e-9:
                occurrence_iso = next_occurrence.isoformat()
                if not _already_notified_insufficient(
                    db, user_id=rule.user_id, rule_id=rule.sync_id, occurrence_iso=occurrence_iso,
                ):
                    notification_service.create_notification(
                        db,
                        user_id=rule.user_id,
                        category="reminder",
                        title=f"自動扣繳未執行：{rule.note or '週期性轉帳'}",
                        body=(
                            f"帳戶餘額不足(需要 {rule.amount:.2f},目前餘額 {balance:.2f}),"
                            f"本期自動扣繳未執行,系統會持續每 15 分鐘重試一次。"
                        ),
                        payload={
                            "ledgerId": notification_service.resolve_ledger_external_id(db, rule.ledger_id),
                            "recurringRuleId": rule.sync_id,
                            "occurrenceAt": occurrence_iso,
                            "kind": "insufficient_funds",
                        },
                    )
                    skipped += 1
                break  # 这期没过就先停在这里,不追后面的期数

            tx_sync_id = new_sync_id("tx")
            item: dict[str, Any] = {
                "syncId": tx_sync_id,
                "type": rule.tx_type,
                "amount": rule.amount,
                "happenedAt": next_occurrence.isoformat(),
                "recurringRuleId": rule.sync_id,
                "createdByUserId": rule.user_id,
                "updatedByUserId": rule.user_id,
                "fromAccountId": rule.from_account_sync_id,
            }
            if rule.note:
                item["note"] = rule.note
            if rule.to_account_sync_id:
                item["toAccountId"] = rule.to_account_sync_id
            # 商家/專案/標籤/手續費/折扣/信用卡回饋(2026-08-17 使用者回饋):
            # 這條「到期才逐筆生成」的自動扣繳路徑一直漏轉發這些欄位,導致
            # transfer 規則生成的每一期交易在畫面上顯示為 `--`/空白——跟
            # `refill_recurring_windows`(288-318 行)那段同款欄位補齊,這裡
            # 補上同一組(categoryId 不適用,transfer 規則本來就沒有分類)。
            if rule.merchant:
                item["merchant"] = rule.merchant
            if rule.project_sync_id:
                item["projectId"] = rule.project_sync_id
            if rule.tag_sync_ids_json:
                try:
                    parsed_tags = json.loads(rule.tag_sync_ids_json)
                    if isinstance(parsed_tags, list):
                        item["tagIds"] = parsed_tags
                except json.JSONDecodeError:
                    pass
            if rule.base_amount is not None:
                item["baseAmount"] = rule.base_amount
            if rule.fee_amount is not None:
                item["feeAmount"] = rule.fee_amount
            if rule.fee_label:
                item["feeLabel"] = rule.fee_label
            if rule.discount_amount is not None:
                item["discountAmount"] = rule.discount_amount
            if rule.discount_label:
                item["discountLabel"] = rule.discount_label
            if rule.reward_rule_sync_ids_json:
                try:
                    parsed_rewards = json.loads(rule.reward_rule_sync_ids_json)
                    if isinstance(parsed_rewards, list):
                        item["rewardRuleIds"] = parsed_rewards
                except json.JSONDecodeError:
                    pass
            emit_tx(db, ledger_id=rule.ledger_id, user_id=rule.user_id, now=now, item=item)
            materialized += 1
            rule.generated_until_at = next_occurrence
            rule_changed = True
            if rule.end_at is not None and rule.generated_until_at >= rule.end_at:
                rule.enabled = False
                break

        if rule_changed:
            _emit_recurring_rule_update(db, rule=rule, now=now)

    if materialized or skipped:
        logger.info(
            "recurring_materializer: transfer rules materialized=%d skipped_insufficient=%d",
            materialized, skipped,
        )
    return {"materialized": materialized, "skipped_insufficient": skipped}


def _lookup_quote_price(db: Session, *, market: str, symbol: str) -> float | None:
    """本地報價快取(`security_quotes`)目前價,沒有就回 None。呼叫前
    `materialize_due_stock_rules` 已經先用 `_refresh_quotes` 對到期規則的
    標的補抓過一次上游(見該函式說明),這裡只負責讀。"""
    ids = securities_store.security_ids(db, [(market.upper(), symbol.upper())])
    security_id = ids.get((market.upper(), symbol.upper()))
    if security_id is None:
        return None
    quote = db.scalar(select(SecurityQuote).where(SecurityQuote.security_id == security_id))
    if quote is None or quote.price is None or quote.price <= 0:
        return None
    return float(quote.price)


def _refresh_quotes(db: Session, keys: list[tuple[str, str]], *, now: datetime) -> None:
    """股票定期定額到期前補抓報價(2026-09-29)。

    以前只讀快取:可是 `security_quote_close` 收盤排程只抓「目前有持股」的
    標的,剛開始定期定額、還沒持有的代號(例如第一期的 0050)快取裡永遠沒有
    報價,每一期都被 `quote_unavailable` 跳過。這裡直接走
    `quotes.get_quotes(refresh=True)`——快取夠新就不打上游,缺價/過期才抓
    (盤中 15 分鐘、盤後 12 小時,同 `/read/securities/quotes` 的規則),抓不到
    就維持快取原值,由呼叫端照舊判斷 quote_unavailable。

    `get_quotes` 打上游前會 `db.rollback()` 放掉連線,所以只能在還沒寫任何
    東西之前呼叫(`materialize_due_stock_rules` 一開始、查完到期規則就呼叫,
    之後重新查一次規則)。已經在 event loop 裡(不該發生:排程/手動觸發都跑在
    worker thread)時直接跳過,不讓整個批次失敗。測試會 monkeypatch 這個
    函式,避免打真的網路。"""
    if not keys:
        return
    import asyncio

    from .securities import quotes as quotes_service

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.warning("recurring_materializer: running inside an event loop, skip quote refresh")
        return
    try:
        asyncio.run(quotes_service.get_quotes(db, keys, refresh=True, now=now))
    except Exception as exc:  # noqa: BLE001 — 報價抓不到不能讓整批定期定額失敗
        logger.warning("recurring_materializer: stock dca quote refresh failed err=%s", exc)
        db.rollback()


# 股票定期定額補期上限(2026-09-29):到期生成只會讀「當下」報價,超過這個
# 天數還沒生成的期數(例如起始日設在一年前、規則停用很久後重新啟用、App 很久
# 沒開)如果照補,每一期都會用今天的價格買進,股數/成本全錯。超過上限的期數
# 直接略過(推進進度、不買),並發一則通知說明。App 端
# `kStockDcaMaxCatchUp` 必須同值。
STOCK_DCA_MAX_CATCH_UP = timedelta(days=7)


def stock_dca_occurrence_ids(rule_sync_id: str, occurrence: datetime) -> tuple[str, str]:
    """股票定期定額每一期生成的 (轉帳交易 syncId, stock_trade syncId)。

    App(`LocalRepository.materializeDueStockRules`)跟 Cloud 都會到期生成,
    而 App 是「啟動時先生成、之後才 pull」——Cloud 15 分鐘排程先生成過、App
    還沒 pull 到就啟動的話,兩邊會各生成一筆。改用「規則 syncId + 該期時間」
    推出來的固定 uuid5,兩邊生成的是同一個 syncId,sync 時只會互相覆蓋
    (LWW),不會變成兩筆。**App 端 `stockDcaOccurrenceIds` 必須用完全一樣的
    字串格式**(namespace = uuid.NAMESPACE_URL,秒級 epoch 無條件捨去),改
    一邊要改另一邊。"""
    from uuid import NAMESPACE_URL, uuid5

    key = f"{rule_sync_id}:{int(_ensure_aware(occurrence).timestamp())}"
    tx_id = str(uuid5(NAMESPACE_URL, f"beecount:stock_dca:tx:{key}"))
    trade_id = str(uuid5(NAMESPACE_URL, f"beecount:stock_dca:trade:{key}"))
    return tx_id, trade_id


def next_pending_occurrence(
    rule: ReadRecurringRuleProjection, advanced_rule: dict[str, Any] | None,
) -> datetime | None:
    """規則下一期要生成的時間(不看是否已到期):還沒生成過 = next_run_at,
    否則 = generated_until_at 之後的下一期。只對「到期才逐筆生成」的規則
    (transfer 自動扣繳 / stock_dca)有意義——這類規則的 next_run_at 建立後
    就不再變動,真正的「下次執行」要從 generated_until_at 往後推;
    `read/ledgers.py::list_recurring_rules` 也用這個算 `upcoming_run_at`。"""
    generated_until = _ensure_aware(rule.generated_until_at) if rule.generated_until_at else None
    if generated_until is None:
        return _ensure_aware(rule.next_run_at)
    following = recurring_schedule.enumerate_occurrences(
        start=generated_until, end=None, frequency=rule.frequency,
        interval=rule.interval, advanced_rule=advanced_rule, max_count=2,
    )
    return following[1] if len(following) > 1 else None


def _decode_advanced_rule(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def emit_stock_trade(
    db: Session, *, ledger_id: str, user_id: str, now: datetime, payload: dict[str, Any],
) -> str:
    """寫一條 stock_trade SyncChange + 同事務 `projection.upsert_stock_trade`。
    同 `emit_tx` 的寫法,只是換一個 entity_type/projection upsert 函式。"""
    trade_sync_id = str(payload["syncId"])
    change_row = SyncChange(
        user_id=user_id,
        ledger_id=ledger_id,
        scope="ledger",
        entity_type="stock_trade",
        entity_sync_id=trade_sync_id,
        action="upsert",
        payload_json=payload,
        updated_at=now,
        updated_by_device_id=_MATERIALIZER_DEVICE_ID,
        updated_by_user_id=user_id,
    )
    db.add(change_row)
    db.flush()
    projection.upsert_stock_trade(
        db, ledger_id=ledger_id, user_id=user_id,
        source_change_id=change_row.change_id, payload=payload,
    )
    return trade_sync_id


def _already_notified_stock(
    db: Session, *, user_id: str, rule_id: str, occurrence_iso: str, reason: str,
) -> bool:
    """同 `_already_notified_insufficient`,但額外用 `reason`
    ('insufficient_funds'/'quote_unavailable')區分——同一期同一個原因只通知
    一次,換了原因(例如上次沒報價這次沒餘額)還是要重新通知一次。"""
    rows = db.scalars(
        select(Notification.payload_json).where(
            Notification.user_id == user_id,
            Notification.category == "reminder",
        )
    ).all()
    for payload in rows:
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("recurringRuleId") == rule_id
            and payload.get("occurrenceAt") == occurrence_iso
            and payload.get("kind") == reason
        ):
            return True
    return False


def materialize_due_stock_rules(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """股票定期定額(`kind='stock_dca'`,2026-09-28,
    docs/STOCK_HOLDINGS_SD.md §9)規則:到期當下才逐筆生成,跟
    `materialize_due_transfer_rules` 同一套「到期查當下資料」的理由,只是多
    一層——除了交割帳戶餘額要夠,還要本地報價快取(`security_quotes`)有這
    檔標的的價格才能算出股數(股數 = 每期投入金額 / 當下報價,允許碎股)。
    兩者任一不滿足就跳過(不推進 `generated_until_at`,下次 15 分鐘 loop
    重試同一期並通知使用者,原因不同各自去重,不會互相覆蓋)。

    生成方式是直接寫 `read_stock_trade_projection`(`emit_stock_trade`)+
    綁定的轉帳交易(`emit_tx`),不透過 `snapshot_mutator.create_stock_trade`
    ——那套「載入整份 ledger snapshot 再 diff」的機制對批次任務太重,
    `materialize_due_transfer_rules` 對一般轉帳規則也是同理直接操作 ORM
    row,不走 snapshot。手續費計算規則(`stock_trade_amount`/
    `trade_fees.suggest_fee` 同款算法)對齊
    `snapshot_mutator.create_stock_trade`/App 端
    `LocalRepository.materializeDueStockRules`,三端改一邊要改另外兩邊。
    手續費 = 規則覆寫(`stock_fee_rate`/`stock_fee_min`)或投資理財帳戶預設
    (`investment_settings_json` 經 `trade_fees.resolve_trade_settings`)。

    不 commit —— 調用方決定事務邊界。返回
    {"materialized": N, "skipped_insufficient": M, "skipped_no_quote": K}。"""
    now = now or datetime.now(timezone.utc)
    stmt = select(ReadRecurringRuleProjection).where(
        ReadRecurringRuleProjection.enabled.is_(True),
        ReadRecurringRuleProjection.kind == "stock_dca",
    )
    # 先挑出到期規則的標的補抓報價(見 _refresh_quotes:它會 rollback,所以
    # 要在任何寫入之前做,做完重新查一次規則)。
    due_keys: list[tuple[str, str]] = []
    for rule in db.scalars(stmt).all():
        if not rule.market or not rule.symbol:
            continue
        nxt = next_pending_occurrence(rule, _decode_advanced_rule(rule.advanced_rule_json))
        if nxt is not None and nxt <= now:
            due_keys.append((rule.market.upper(), rule.symbol.upper()))
    if due_keys:
        _refresh_quotes(db, list(dict.fromkeys(due_keys)), now=now)
    rules = db.scalars(stmt).all()

    materialized = 0
    skipped_balance = 0
    skipped_quote = 0
    skipped_stale = 0
    for rule in rules:
        if (
            not rule.from_account_sync_id
            or not rule.to_account_sync_id
            or not rule.market
            or not rule.symbol
        ):
            continue  # 資料異常防禦:stock_dca 規則理論上一定帶齊這幾個欄位
        lock_ledger_for_materialize(db, rule.ledger_id)
        rule.next_run_at = _ensure_aware(rule.next_run_at)
        if rule.end_at is not None:
            rule.end_at = _ensure_aware(rule.end_at)

        advanced_rule = _decode_advanced_rule(rule.advanced_rule_json)

        investment_account = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == rule.user_id,
                UserAccountProjection.sync_id == rule.to_account_sync_id,
            )
        )
        if investment_account is None:
            continue

        market_info = markets.get_market(rule.market)
        security_currency = (
            (market_info.currency if market_info else None)
            or investment_account.currency
        )
        security_currency = security_currency.upper() if security_currency else None

        settings: dict[str, Any] = {}
        if investment_account.investment_settings_json:
            try:
                parsed_settings = json.loads(investment_account.investment_settings_json)
                if isinstance(parsed_settings, dict):
                    settings = parsed_settings
            except json.JSONDecodeError:
                settings = {}
        resolved = trade_fees.resolve_trade_settings(rule.market, settings)
        fee_rate = rule.stock_fee_rate if rule.stock_fee_rate is not None else resolved["feeRate"]
        fee_min = rule.stock_fee_min if rule.stock_fee_min is not None else resolved["feeMin"]
        fee_discount = resolved["feeDiscount"]

        rule_changed = False
        stale_skipped: list[datetime] = []
        while True:
            next_occurrence = next_pending_occurrence(rule, advanced_rule)
            if next_occurrence is None or next_occurrence > now:
                break

            if rule.end_at is not None and next_occurrence > rule.end_at:
                rule.generated_until_at = rule.end_at
                rule.enabled = False
                rule_changed = True
                break

            if next_occurrence < now - STOCK_DCA_MAX_CATCH_UP:
                rule.generated_until_at = next_occurrence
                rule_changed = True
                stale_skipped.append(next_occurrence)
                if rule.end_at is not None and rule.generated_until_at >= rule.end_at:
                    rule.enabled = False
                    break
                continue

            tx_sync_id, trade_sync_id = stock_dca_occurrence_ids(rule.sync_id, next_occurrence)
            # App 已經生成過這一期(同一組固定 syncId)並推上來了:不重複生成,
            # 只把進度推進到這一期。
            if db.scalar(
                select(ReadStockTradeProjection.sync_id).where(
                    ReadStockTradeProjection.ledger_id == rule.ledger_id,
                    ReadStockTradeProjection.sync_id == trade_sync_id,
                )
            ) is not None:
                rule.generated_until_at = next_occurrence
                rule_changed = True
                if rule.end_at is not None and rule.generated_until_at >= rule.end_at:
                    rule.enabled = False
                    break
                continue

            price = _lookup_quote_price(db, market=rule.market, symbol=rule.symbol)
            if price is None:
                occurrence_iso = next_occurrence.isoformat()
                if not _already_notified_stock(
                    db, user_id=rule.user_id, rule_id=rule.sync_id,
                    occurrence_iso=occurrence_iso, reason="quote_unavailable",
                ):
                    notification_service.create_notification(
                        db,
                        user_id=rule.user_id,
                        category="reminder",
                        title=f"定期定額未執行：{rule.symbol}",
                        body="目前沒有這檔標的的報價,本期定期定額未執行,系統會持續每 15 分鐘重試一次。",
                        payload={
                            "ledgerId": notification_service.resolve_ledger_external_id(db, rule.ledger_id),
                            "recurringRuleId": rule.sync_id,
                            "occurrenceAt": occurrence_iso,
                            "kind": "quote_unavailable",
                        },
                    )
                    skipped_quote += 1
                break  # 這期沒過就先停在這裡,不追後面的期數

            gross = trade_fees.round_money(rule.amount, security_currency)
            fee = max(trade_fees.round_money(gross * fee_rate * fee_discount, security_currency), fee_min)
            shares = gross / price

            balance = compute_account_balance(
                db, user_id=rule.user_id, account_sync_id=rule.from_account_sync_id, now=now,
            )
            required = gross + fee
            if balance < required - 1e-9:
                occurrence_iso = next_occurrence.isoformat()
                if not _already_notified_stock(
                    db, user_id=rule.user_id, rule_id=rule.sync_id,
                    occurrence_iso=occurrence_iso, reason="insufficient_funds",
                ):
                    notification_service.create_notification(
                        db,
                        user_id=rule.user_id,
                        category="reminder",
                        title=f"定期定額未執行：{rule.symbol}",
                        body=(
                            f"交割帳戶餘額不足(需要 {required:.2f},目前餘額 {balance:.2f}),"
                            f"本期定期定額未執行,系統會持續每 15 分鐘重試一次。"
                        ),
                        payload={
                            "ledgerId": notification_service.resolve_ledger_external_id(db, rule.ledger_id),
                            "recurringRuleId": rule.sync_id,
                            "occurrenceAt": occurrence_iso,
                            "kind": "insufficient_funds",
                        },
                    )
                    skipped_balance += 1
                break  # 這期沒過就先停在這裡,不追後面的期數

            note = rule.note or f"定期定額 {rule.symbol} {shares:g}股"
            tx_item: dict[str, Any] = {
                "syncId": tx_sync_id,
                "type": "transfer",
                "amount": gross,
                "happenedAt": next_occurrence.isoformat(),
                "fromAccountId": rule.from_account_sync_id,
                "toAccountId": rule.to_account_sync_id,
                "note": note,
                "recurringRuleId": rule.sync_id,
                "createdByUserId": rule.user_id,
                "updatedByUserId": rule.user_id,
            }
            if fee > 0:
                tx_item["feeAmount"] = fee
            emit_tx(db, ledger_id=rule.ledger_id, user_id=rule.user_id, now=now, item=tx_item)

            trade_payload: dict[str, Any] = {
                "syncId": trade_sync_id,
                "accountId": rule.to_account_sync_id,
                "market": rule.market,
                "symbol": rule.symbol,
                "tradeType": "buy",
                "shares": shares,
                "price": price,
                "fee": fee,
                "tax": 0.0,
                "amount": stock_trade_amount("buy", shares, price, fee, 0.0, security_currency),
                "tradeDate": next_occurrence.isoformat(),
                "txId": tx_sync_id,
                "createdByUserId": rule.user_id,
            }
            if security_currency:
                trade_payload["currency"] = security_currency
            if rule.security_name:
                trade_payload["securityName"] = rule.security_name
            if note:
                trade_payload["note"] = note
            emit_stock_trade(db, ledger_id=rule.ledger_id, user_id=rule.user_id, now=now, payload=trade_payload)

            materialized += 1
            rule.generated_until_at = next_occurrence
            rule_changed = True
            if rule.end_at is not None and rule.generated_until_at >= rule.end_at:
                rule.enabled = False
                break

        if stale_skipped:
            skipped_stale += len(stale_skipped)
            last_iso = stale_skipped[-1].isoformat()
            if not _already_notified_stock(
                db, user_id=rule.user_id, rule_id=rule.sync_id,
                occurrence_iso=last_iso, reason="stale_skipped",
            ):
                notification_service.create_notification(
                    db,
                    user_id=rule.user_id,
                    category="reminder",
                    title=f"定期定額已略過過期的期數：{rule.symbol}",
                    body=(
                        f"有 {len(stale_skipped)} 期超過 {STOCK_DCA_MAX_CATCH_UP.days} 天未執行"
                        f"(最早 {stale_skipped[0].date().isoformat()}),無法取得當時的價格,"
                        "已略過不補買;如有實際成交請到投資頁手動新增。"
                    ),
                    payload={
                        "ledgerId": notification_service.resolve_ledger_external_id(db, rule.ledger_id),
                        "recurringRuleId": rule.sync_id,
                        "occurrenceAt": last_iso,
                        "kind": "stale_skipped",
                    },
                )

        if rule_changed:
            _emit_recurring_rule_update(db, rule=rule, now=now)

    if materialized or skipped_balance or skipped_quote:
        logger.info(
            "recurring_materializer: stock dca rules materialized=%d skipped_insufficient=%d skipped_no_quote=%d",
            materialized, skipped_balance, skipped_quote,
        )
    return {
        "materialized": materialized,
        "skipped_insufficient": skipped_balance,
        "skipped_no_quote": skipped_quote,
        "skipped_stale": skipped_stale,
    }


def materialize_all_due(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """一次性跑完週期性收支的視窗續產生并 commit。给 internal endpoint 用
    —— 自成一个事务,失败整体回滚不留半截数据。分期付款不再需要排程(建
    计画/rebalance/payoff 等写入口当下就算完全部期数),这里不再调用任何
    installment 相关函式。main.py 的周期性 asyncio loop 里
    `materialize_due_transfer_rules` 是挂在另一个更高频的 loop 上单独跑的
    (见 main.py 顶部说明),不在这里重复调用。"""
    now = now or datetime.now(timezone.utc)
    recurring_count = refill_recurring_windows(db, now=now)
    db.commit()
    return {"recurring_transactions": recurring_count}
