"""交易对手(counterparty)维度统计 endpoint。

GET /api/v1/ledgers/{ledger_external_id}/counterparty-stats
  ?scope=month|year|all  &period=&tz_offset_minutes=  &status=all|active|repaid|cancelled
  → 按借款(0023)的 counterparty 维度聚合「借入/借出」。

口径(用户需求:交易对手也是一个统计角度,借入借出可以对充才是平帐):
  - 以 read_loan_projection 为数据源;counterparty 是借款里的「对方」(人/机构),
    自由文本,按 trimmed+lower 归一归组,展示名取组内首个出现值。
  - 每个交易对手汇总:
      * lent_total      = direction='lent'(借出,别人欠我)的本金合计
      * borrowed_total  = direction='borrowed'(借入,我欠别人)的本金合计
      * net            = lent_total - borrowed_total
                          >0 → 对方净欠我(我是净债权人)
                          <0 → 我净欠对方
      * count          = 借款笔数
  - 这就是「平帐」视角:同一人身上我借出去的 + 借进来的互相抵消,剩下的净额
    就是还该跟 TA 算清的数目。

scope 作用于 loan.started_at;status 可过滤只看在贷/在借(active)以便看未结清净额。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..ledger_access import require_accessible_ledger_by_external_id
from ..models import ReadLoanProjection, User
from ..schemas import AnalyticsScope
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ
from .read._shared import _analytics_range

router = APIRouter()
_READ_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ)


class CounterpartyStatItem(BaseModel):
    """交易对手维度统计的一行。

    net = lent_total - borrowed_total。正数表示对方净欠我,负数表示我净欠对方。
    """

    counterparty: str
    lent_total: float = 0.0
    borrowed_total: float = 0.0
    net: float = 0.0
    loan_count: int = 0


class CounterpartyStatsResponse(BaseModel):
    ledger_id: str
    ledger_currency: str
    scope: AnalyticsScope
    period: str | None
    status: str
    start_at: datetime | None
    end_at: datetime | None
    items: list[CounterpartyStatItem] = Field(default_factory=list)


@router.get(
    "/ledgers/{ledger_external_id}/counterparty-stats",
    response_model=CounterpartyStatsResponse,
)
def get_counterparty_stats(
    ledger_external_id: str,
    scope: AnalyticsScope = Query(default="all"),
    period: str | None = Query(default=None),
    tz_offset_minutes: int = Query(default=0, ge=-720, le=840),
    status: str = Query(
        default="all",
        description="all | active | repaid | cancelled。默认 all 统计全部借款。",
    ),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CounterpartyStatsResponse:
    ledger, _role = require_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
    )
    start_at, end_at, normalized_period = _analytics_range(
        scope=scope, period=period, tz_offset_minutes=tz_offset_minutes,
        month_start_day=ledger.month_start_day or 1,
    )

    # GROUP BY counterparty(归一) 一次聚出 借出合计 / 借入合计 / 笔数。
    # 用 CASE 把两个方向拆成两列,避免分两次查询。
    normalized = func.lower(func.trim(ReadLoanProjection.counterparty))
    stmt = select(
        normalized.label("cp_key"),
        ReadLoanProjection.counterparty,
        func.sum(
            case((ReadLoanProjection.direction == "lent", ReadLoanProjection.principal), else_=0.0)
        ).label("lent_total"),
        func.sum(
            case(
                (ReadLoanProjection.direction == "borrowed", ReadLoanProjection.principal),
                else_=0.0,
            )
        ).label("borrowed_total"),
        func.count().label("loan_count"),
    ).where(
        ReadLoanProjection.ledger_id == ledger.id,
    )
    if start_at is not None:
        stmt = stmt.where(ReadLoanProjection.started_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(ReadLoanProjection.started_at < end_at)
    if status and status != "all":
        stmt = stmt.where(ReadLoanProjection.status == status)
    stmt = stmt.group_by(normalized, ReadLoanProjection.counterparty)

    rows = db.execute(stmt).all()

    # 同一归一 key 下可能有多条原始 counterparty 写法(大小写/空格不同),
    # 用首个出现值作展示名(保持 DB 返回顺序)。
    display_by_key: dict[str, str] = {}
    agg: dict[str, list] = {}
    for cp_key, raw_name, lent, borrowed, count in rows:
        if cp_key is None:
            continue
        key = str(cp_key)
        if key not in display_by_key:
            display_by_key[key] = raw_name or key
        bucket = agg.setdefault(key, [0.0, 0.0, 0])
        bucket[0] += float(lent or 0.0)
        bucket[1] += float(borrowed or 0.0)
        bucket[2] += int(count or 0)

    items: list[CounterpartyStatItem] = []
    for key, (lent_total, borrowed_total, count) in agg.items():
        items.append(
            CounterpartyStatItem(
                counterparty=display_by_key[key],
                lent_total=lent_total,
                borrowed_total=borrowed_total,
                net=lent_total - borrowed_total,
                loan_count=int(count),
            )
        )

    # 排序:净额绝对值降序(最该算清的排前面),再按名称兜底。
    items.sort(key=lambda x: (-abs(x.net), x.counterparty))

    return CounterpartyStatsResponse(
        ledger_id=ledger_external_id,
        ledger_currency=ledger.currency or "CNY",
        scope=scope,
        period=normalized_period,
        status=status,
        start_at=start_at,
        end_at=end_at,
        items=items,
    )
