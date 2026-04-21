"""/summary —— 全账户 / 全账本的用户总览读。小端点,独成一文件
避免跟 ledgers / workspace 混淆。"""
from __future__ import annotations

from ._shared import *  # noqa: F401,F403 — imports + helpers + router

@router.get("/summary", response_model=ReadSummaryOut)
def get_summary(
    ledger_id: str = Query(..., min_length=1),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadSummaryOut:
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_id,
        is_admin=is_admin,
    )
    tx_count, income_total, expense_total, latest_happened_at = _projection_totals(db, ledger.id)

    return ReadSummaryOut(
        ledger_id=ledger_id,
        transaction_count=tx_count,
        income_total=income_total,
        expense_total=expense_total,
        balance=income_total - expense_total,
        latest_happened_at=latest_happened_at,
    )


