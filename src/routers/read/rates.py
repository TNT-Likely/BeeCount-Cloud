"""汇率读端点:手动 override 列表(本文件) + 汇率代理(Task 5 追加)。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...database import get_db
from ...deps import get_current_user
from ...models import User, UserExchangeRateProjection
from ...routers.pats import _utc_iso  # SQLite naive-datetime 坑,同 pats.py:75-87
from ...services.exchange_rate import fetcher
from ._shared import _READ_SCOPE_DEP, router


class ExchangeRateOverrideOut(BaseModel):
    sync_id: str
    base_currency: str
    quote_currency: str
    rate: str
    updated_at: str


@router.get("/exchange-rate-overrides", response_model=list[ExchangeRateOverrideOut])
def list_exchange_rate_overrides(
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ExchangeRateOverrideOut]:
    rows = db.scalars(
        select(UserExchangeRateProjection)
        .where(UserExchangeRateProjection.user_id == current_user.id)
        .order_by(
            UserExchangeRateProjection.quote_currency,
            UserExchangeRateProjection.sync_id,
        )
    ).all()
    return [
        ExchangeRateOverrideOut(
            sync_id=r.sync_id,
            base_currency=r.base_currency,
            quote_currency=r.quote_currency,
            rate=r.rate,
            updated_at=_utc_iso(r.updated_at) or "",  # 列 NOT NULL,or "" 仅为类型收敛
        )
        for r in rows
    ]


class ExchangeRatesOut(BaseModel):
    base: str
    rate_date: str
    source: str
    fetched_at: str
    stale: bool
    rates: dict[str, str]


@router.get("/exchange-rates", response_model=ExchangeRatesOut)
async def get_exchange_rates(
    # base 必须先过格式校验:非法值直接 422,不进 fetcher 的 _locks(防垃圾 base
    # 撑爆进程内锁字典)、不拼上游 URL(防 query 注入)、不落 exchange_rate_cache。
    # pattern 与 override 端点对齐(见 .docs/multi-currency/06-exchange-rate-security-review.md P0)。
    base: str = Query(pattern=r"^[A-Za-z]{3,8}$"),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExchangeRatesOut:
    settings = get_settings()
    if not settings.exchange_rate_proxy_enabled:
        raise HTTPException(status_code=404, detail="exchange rate proxy disabled")
    try:
        row, stale = await fetcher.get_rates(db, base)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ExchangeRatesOut(
        base=row.base_currency,
        rate_date=row.rate_date,
        source=row.source,
        fetched_at=_utc_iso(row.fetched_at) or "",  # 列 NOT NULL,or "" 仅为类型收敛
        stale=stale,
        rates=dict(row.payload_json),
    )
