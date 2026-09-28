"""内部排程任务的手动触发端点(MOZE_FEATURE_GAP_SD.md §2.2/§2.3 Phase 1)。

`services.recurring_materializer` 平时由 main.py 的周期性 asyncio loop 调用,
这里额外暴露一个 admin-only endpoint,给:
  - 外部 cron(不想在本进程里维护 asyncio loop 时,改成外部定时 curl 这个
    endpoint,文档 §2.2 提到的"或外部 cron 打一个 /internal/tasks/
    materialize-recurring 端点"就是这个)
  - 手动 / 测试环境立即触发一次物化,不用等下一个 loop tick

要求 admin scope,不对普通用户开放 —— 这是运维操作,不是业务 API。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin_user
from ..models import User
from ..services import (
    card_reward_payout,
    credit_card_autopay,
    credit_card_reminders,
    debt_reminders,
    recurring_materializer,
)

router = APIRouter()


@router.post("/tasks/materialize-recurring")
def materialize_recurring(
    _admin: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict:
    result = recurring_materializer.materialize_all_due(db)
    # 自動扣繳(tx_type=="transfer",2026-08-02 补):独立于批次视窗续产生
    # (materialize_all_due 只处理非 transfer 规则),复用同一个手动触发端点。
    transfer_result = recurring_materializer.materialize_due_transfer_rules(db)
    if transfer_result["materialized"] or transfer_result["skipped_insufficient"]:
        db.commit()
    result["transfer_rules_materialized"] = transfer_result["materialized"]
    result["transfer_rules_skipped_insufficient"] = transfer_result["skipped_insufficient"]
    # 股票定期定額(2026-09-28),同一個道理復用這個手動觸發端點。
    stock_result = recurring_materializer.materialize_due_stock_rules(db)
    if (
        stock_result["materialized"]
        or stock_result["skipped_insufficient"]
        or stock_result["skipped_no_quote"]
    ):
        db.commit()
    result["stock_dca_materialized"] = stock_result["materialized"]
    result["stock_dca_skipped_insufficient"] = stock_result["skipped_insufficient"]
    result["stock_dca_skipped_no_quote"] = stock_result["skipped_no_quote"]
    # 借還款追蹤(§2.5 Phase 3)到期提醒,复用同一个手动触发端点,不需要独立
    # admin-only endpoint(见 debt_reminders.py 模块说明)。
    debt_reminder_count = debt_reminders.send_due_debt_reminders(db)
    if debt_reminder_count:
        db.commit()
    result["debt_reminders"] = debt_reminder_count
    # 信用卡繳款到期提醒(§2.9 Phase 4),同一个道理复用这个手动触发端点。
    card_reminder_count = credit_card_reminders.send_due_card_reminders(db)
    if card_reminder_count:
        db.commit()
    result["card_reminders"] = card_reminder_count
    # 自動扣繳(§2.9,2026-08-04 改版:帳戶級開關,不再是週期性收支規則),
    # 同一个道理复用这个手动触发端点。
    autopay_result = credit_card_autopay.materialize_due_card_autopay(db)
    if autopay_result["executed"] or autopay_result["skipped_insufficient"]:
        db.commit()
    result["card_autopay_executed"] = autopay_result["executed"]
    result["card_autopay_skipped_insufficient"] = autopay_result["skipped_insufficient"]
    # 信用卡紅利回饋自動入帳(§2.9.5.4),同一个道理复用这个手动触发端点。
    reward_payout_result = card_reward_payout.materialize_due_card_reward_payouts(db)
    if reward_payout_result["tx_payouts"] or reward_payout_result["period_payouts"]:
        db.commit()
    result["card_reward_tx_payouts"] = reward_payout_result["tx_payouts"]
    result["card_reward_period_payouts"] = reward_payout_result["period_payouts"]
    return result
