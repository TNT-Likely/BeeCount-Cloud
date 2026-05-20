"""共享账本成员收支统计 endpoint。

GET /api/v1/ledgers/{ledger_external_id}/member-stats
  ?scope=month|year|all  &period=&tz_offset_minutes=

按 read_tx_projection.created_by_user_id 维度 GROUP BY 聚合 income / expense /
tx_count,返回该账本所有 LedgerMember 的本期统计。即使某成员本期未记账,
也返回一行 (0, 0, 0),让 UI 能完整展示成员名单。

口径:**以 tx 创建人为准**(created_by_user_id),"上次编辑人"不算。理由:
谁记的就算谁花的 / 挣的;编辑只改字段不改归属。

跳过 transfer:transfer tx 既非 income 也非 expense,不计入。
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..ledger_access import (
    list_ledger_members,
    require_accessible_ledger_by_external_id,
)
from ..models import ReadTxProjection, User
from ..schemas import AnalyticsScope
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ
from .read._shared import _analytics_range, _user_info_map

router = APIRouter()
logger = logging.getLogger(__name__)

_READ_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ)


class MemberStatItem(BaseModel):
    user_id: str
    email: str | None
    display_name: str | None
    avatar_url: str | None
    avatar_version: int = 0
    role: str
    income_total: float = 0.0
    expense_total: float = 0.0
    tx_count: int = 0


class MemberStatsResponse(BaseModel):
    ledger_id: str
    ledger_currency: str
    scope: AnalyticsScope
    period: str | None
    start_at: datetime | None
    end_at: datetime | None
    items: list[MemberStatItem] = Field(default_factory=list)


@router.get(
    "/ledgers/{ledger_external_id}/member-stats",
    response_model=MemberStatsResponse,
)
def get_member_stats(
    ledger_external_id: str,
    scope: AnalyticsScope = Query(default="month"),
    period: str | None = Query(default=None),
    tz_offset_minutes: int = Query(default=0, ge=-720, le=840),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberStatsResponse:
    ledger, _role = require_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
    )

    start_at, end_at, normalized_period = _analytics_range(
        scope=scope, period=period, tz_offset_minutes=tz_offset_minutes
    )

    # GROUP BY created_by_user_id 一次聚合 income / expense / tx_count
    q = select(
        ReadTxProjection.created_by_user_id,
        func.sum(
            case(
                (ReadTxProjection.tx_type == "income", ReadTxProjection.amount),
                else_=0.0,
            )
        ).label("income_total"),
        func.sum(
            case(
                (ReadTxProjection.tx_type == "expense", ReadTxProjection.amount),
                else_=0.0,
            )
        ).label("expense_total"),
        func.count().label("tx_count"),
    ).where(
        ReadTxProjection.ledger_id == ledger.id,
        ReadTxProjection.tx_type.in_(("income", "expense")),  # 跳过 transfer
        ReadTxProjection.created_by_user_id.is_not(None),
    )
    if start_at is not None:
        q = q.where(ReadTxProjection.happened_at >= start_at)
    if end_at is not None:
        q = q.where(ReadTxProjection.happened_at < end_at)
    q = q.group_by(ReadTxProjection.created_by_user_id)

    rows = db.execute(q).all()
    stats_by_uid: dict[str, tuple[float, float, int]] = {
        row[0]: (float(row[1] or 0.0), float(row[2] or 0.0), int(row[3] or 0))
        for row in rows
    }

    # 拿全成员名单(包括本期无记账的) + 用户信息批量
    members = list_ledger_members(db, ledger_id=ledger.id)
    member_user_ids = {uid for uid, _role in members}
    # rows 里 created_by 可能不在 LedgerMember(被踢的老成员的 tx 仍保留),
    # 也算上 — 显示成员历史贡献。
    all_user_ids = member_user_ids | set(stats_by_uid.keys())
    user_info = _user_info_map(db, all_user_ids)
    role_by_uid = {uid: role for uid, role in members}

    items: list[MemberStatItem] = []
    for uid in all_user_ids:
        info = user_info.get(uid, (None, None, None, 0))
        email, display_name, avatar_file_id, avatar_version = info
        avatar_url = (
            f"/api/v1/profile/avatar/{uid}?v={avatar_version}"
            if avatar_file_id
            else None
        )
        income, expense, tx_count = stats_by_uid.get(uid, (0.0, 0.0, 0))
        items.append(
            MemberStatItem(
                user_id=uid,
                email=email,
                display_name=display_name,
                avatar_url=avatar_url,
                avatar_version=avatar_version if avatar_file_id else 0,
                # 老成员被踢后 LedgerMember 里没了,但 tx 还在,标 "removed"
                role=role_by_uid.get(uid, "removed"),
                income_total=income,
                expense_total=expense,
                tx_count=tx_count,
            )
        )

    # 排序:支出降序、收入降序、笔数降序、user_id 兜底
    items.sort(
        key=lambda x: (-x.expense_total, -x.income_total, -x.tx_count, x.user_id)
    )

    logger.info(
        "member-stats.get ledger=%s scope=%s period=%s rows=%d caller=%s",
        ledger_external_id,
        scope,
        normalized_period,
        len(items),
        current_user.id,
    )

    return MemberStatsResponse(
        ledger_id=ledger_external_id,
        ledger_currency=ledger.currency or "CNY",
        scope=scope,
        period=normalized_period,
        start_at=start_at,
        end_at=end_at,
        items=items,
    )
