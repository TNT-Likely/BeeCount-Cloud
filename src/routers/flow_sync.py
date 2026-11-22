"""交易流水变更 → 引用实体同步(0042)。

背景:系统里有多个业务功能会**引用**某条交易流水,并据此做统计与金额计算。
当用户直接编辑一条流水的金额时,这些引用实体必须跟着变,否则会出现
「流水显示 300,但借款记录还写着 299.85」这类账实不符。

各功能的处理策略不同,本模块统一调度:

| 引用方 | 金额来源 | 同步策略 |
|---|---|---|
| 借款本金(Loan) | 快照 loan.amount | 改流水 → 改 loan.amount + 重算状态 |
| 借款还款(LoanRepayment) | 快照 repayment.amount | 改流水 → 改本金部分 + 重算状态 |
| 合并还款组(LoanPaymentGroup) | 组内分摊行之和 | 改流水 → 按比例调整组内分摊行 |
| 储蓄罐存入(SavingContribution) | **实时派生** | 无需同步(读时从投影派生,自动跟随) |
| 物品(Item) | 快照 item.price | 改流水 → 改价格(仅当该流水是唯一关联) |
| 物品流水(ItemFlow) | 快照 flow.amount | 改流水 → 改 flow.amount |
| 报销(Reimbursement) | 快照 rb.amount | 改流水 → 改 rb.amount |

设计要点:
- 只在**金额/优惠**变化时触发(改备注/分类不需要同步金额);
- 各模块的同步函数放在自己的 router 里,本模块只做调度,避免循环依赖;
- 同步失败不影响流水本身(流水是用户的直接意图,优先级最高);
- **流水金额以已提交的投影为准**(read_tx_projection),这样「只改优惠」也能
  算出正确结果,不必依赖调用方传入的 new_amount。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ReadTxProjection

logger = logging.getLogger(__name__)


def _read_flow_amounts(
    db: Session, *, ledger_id: str, tx_sync_id: str
) -> tuple[float | None, float]:
    """读取流水当前的 (金额, 优惠额)。

    投影(read_tx_projection)在写入时已同步更新,是最新的可信来源。
    找不到时返回 (None, 0.0),由调用方决定如何兜底。
    """
    flow = db.scalar(
        select(ReadTxProjection).where(
            ReadTxProjection.sync_id == tx_sync_id,
            ReadTxProjection.ledger_id == ledger_id,
        )
    )
    if flow is None:
        return None, 0.0
    return float(flow.amount or 0), float(flow.discount_amount or 0)


def sync_referenced_entities_from_flow(
    db: Session, *, ledger_id: str, tx_sync_id: str, new_amount: float | None
) -> list[str]:
    """流水金额/优惠变更 → 同步所有引用该流水的业务实体。

    返回被同步的实体描述列表(便于日志/测试断言)。
    """
    amount, discount = _read_flow_amounts(
        db, ledger_id=ledger_id, tx_sync_id=tx_sync_id
    )
    # 投影读不到时退回调用方传入的金额(兜底)。
    if amount is None or amount <= 0:
        if new_amount is None:
            return []
        try:
            amount = float(new_amount)
        except (TypeError, ValueError):
            return []
    if amount <= 0:
        return []

    touched: list[str] = []

    # --- 借款 / 还款(含合并组) ---
    try:
        from .contacts_loans import sync_loan_from_flow_edit

        if sync_loan_from_flow_edit(
            db,
            ledger_id=ledger_id,
            tx_sync_id=tx_sync_id,
            new_amount=amount,
            discount_amount=discount,
        ):
            touched.append("loan")
    except Exception:
        logger.exception("sync loan from flow edit failed: %s", tx_sync_id)
        db.rollback()

    # --- 物品(价格 + 物品流水) ---
    try:
        from .items import sync_item_from_flow_edit

        touched.extend(
            sync_item_from_flow_edit(
                db, ledger_id=ledger_id, tx_sync_id=tx_sync_id, new_amount=amount
            )
        )
    except Exception:
        logger.exception("sync item from flow edit failed: %s", tx_sync_id)
        db.rollback()

    # --- 报销 ---
    try:
        from .reimbursements import sync_reimbursement_from_flow_edit

        if sync_reimbursement_from_flow_edit(
            db, ledger_id=ledger_id, tx_sync_id=tx_sync_id, new_amount=amount
        ):
            touched.append("reimbursement")
    except Exception:
        logger.exception("sync reimbursement from flow edit failed: %s", tx_sync_id)
        db.rollback()

    # 储蓄罐金额是**实时派生**(saving_plans._effective_amounts 读投影),
    # 流水金额一变储蓄罐金额自动跟随,无需在这里同步。
    return touched
