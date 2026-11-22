"""用户级锁账守卫(0067)。

设计要点
--------
交易写入在本项目里最终都汇聚到 projection.upsert_tx / projection.delete_tx
（write 端点、sync push、借款/储蓄罐/报销/物品/定期/import/MCP 均如此），
因此把校验放在投影层即可一次覆盖所有入口，不必逐条业务路由打补丁。

账户是 user-global 实体（无 ledger_id），初始金额变更汇聚到
projection.upsert_account；同样在这一层校验。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from .models import ReadTxProjection, User, UserAccountProjection

LOCKED_TX_DETAIL = "锁账期间禁止操作：该日期及之前的交易已冻结，请先解锁账本"
LOCKED_ACCOUNT_DETAIL = "锁账期间禁止修改账户初始金额，请先解锁账本"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _lock_state(db: Session, user_id: str) -> tuple[bool, datetime | None]:
    user = db.get(User, user_id)
    if user is None:
        return False, None
    return bool(user.is_locked), user.lock_date


def _as_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def assert_tx_mutable(db: Session, user_id: str, happened_at: datetime | None) -> None:
    """校验某笔交易当前是否可写。`happened_at` 为 None 时放行（视为当期）。"""
    locked, lock_date = _lock_state(db, user_id)
    if not locked or lock_date is None or happened_at is None:
        return
    if _as_utc(happened_at) <= _as_utc(lock_date):
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=LOCKED_TX_DETAIL)


def assert_tx_delete_mutable(db: Session, ledger_id: str, sync_id: str) -> None:
    """删除路径只有 ledger_id + sync_id，先从投影行反查用户与交易日期。"""
    row = db.get(ReadTxProjection, (ledger_id, sync_id))
    if row is None:
        return
    assert_tx_mutable(db, row.user_id, row.happened_at)


def assert_account_initial_balance_mutable(
    db: Session, user_id: str, sync_id: str, payload: dict[str, Any]
) -> None:
    """锁定期间禁止改动已有账户的初始金额；新建账户（尚无投影行）不受限。"""
    locked, _ = _lock_state(db, user_id)
    if not locked:
        return
    existing = db.get(UserAccountProjection, (user_id, sync_id))
    if existing is None:
        return
    old = float(existing.initial_balance or 0.0)
    new = _as_float(payload.get("initialBalance"))
    if abs(new - old) > 1e-9:
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=LOCKED_ACCOUNT_DETAIL)
