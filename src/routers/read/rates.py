"""汇率读端点:手动 override 列表(本文件) + 汇率代理(Task 5 追加)。"""
from __future__ import annotations

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...database import get_db
from ...deps import get_current_user
from ...models import User, UserExchangeRateProjection
from ...routers.pats import _utc_iso  # SQLite naive-datetime 坑,同 pats.py:75-87
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
            updated_at=_utc_iso(r.updated_at),
        )
        for r in rows
    ]
