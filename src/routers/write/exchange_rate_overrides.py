"""Web 端手动汇率写入 — 不走 ledger snapshot 写引擎(_commit_write)。

exchange_rate_override 是 user-global 实体且不属于任何账本 snapshot,走
「sync push 等价」旁路:落 SyncChange(scope='user') + 投 projection,
App 经正常 /sync/pull 收敛(App 端按币对 upsert)。
设计:BeeCount 仓 .docs/multi-currency/03-tech-design-cloud.md §六。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...database import get_db
from ...deps import get_current_user
from ...models import SyncChange, User, UserExchangeRateProjection
from ...sync_applier import apply_user_change_to_projection
from ._shared import _WRITE_SCOPE_DEP

logger = logging.getLogger(__name__)

router = APIRouter()


class ExchangeRateOverridePutRequest(BaseModel):
    base_currency: str = Field(pattern=r"^[A-Za-z]{3,8}$")
    quote_currency: str = Field(pattern=r"^[A-Za-z]{3,8}$")
    rate: str = Field(min_length=1, max_length=32)


class ExchangeRateOverrideWriteOut(BaseModel):
    sync_id: str
    base_currency: str
    quote_currency: str
    rate: str | None = None


def _find_pair(
    db: Session, user_id: str, base: str, quote: str
) -> UserExchangeRateProjection | None:
    return db.scalar(
        select(UserExchangeRateProjection).where(
            UserExchangeRateProjection.user_id == user_id,
            UserExchangeRateProjection.base_currency == base,
            UserExchangeRateProjection.quote_currency == quote,
        )
    )


def _emit_change(
    db: Session,
    *,
    user_id: str,
    sync_id: str,
    action: str,
    payload: dict | None,
    device_id: str,
) -> SyncChange:
    change = SyncChange(
        user_id=user_id,
        ledger_id=None,
        scope="user",
        entity_type="exchange_rate_override",
        entity_sync_id=sync_id,
        action=action,
        payload_json=payload or {},
        updated_at=datetime.now(timezone.utc),
        updated_by_device_id=device_id,
        updated_by_user_id=user_id,
    )
    db.add(change)
    db.flush()
    return change


async def _broadcast_user_sync_change(
    request: Request, user_id: str, server_cursor: int
) -> None:
    """通知在线 App 立即 pull __user_global__。失败不 break。"""
    try:
        ws_manager = getattr(request.app.state, "ws_manager", None)
        if ws_manager is None:
            return
        await ws_manager.broadcast_to_user(
            user_id,
            {
                "type": "sync_change",
                "ledgerId": "__user_global__",
                "serverCursor": server_cursor,
                "serverTimestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "exchange_rate_override: ws broadcast failed user=%s err=%s", user_id, exc
        )


@router.put("/exchange-rate-overrides", response_model=ExchangeRateOverrideWriteOut)
async def put_exchange_rate_override(
    req: ExchangeRateOverridePutRequest,
    request: Request,
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExchangeRateOverrideWriteOut:
    base = req.base_currency.upper()
    quote = req.quote_currency.upper()
    if base == quote:
        raise HTTPException(status_code=422, detail="base and quote must differ")
    try:
        rate_val = float(req.rate)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="rate must be a number") from exc
    if not (0.000001 < rate_val < 1e9):
        raise HTTPException(status_code=422, detail="rate out of range")

    existing = _find_pair(db, current_user.id, base, quote)
    sync_id = existing.sync_id if existing is not None else f"rate-{uuid4()}"
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "syncId": sync_id,
        "baseCurrency": base,
        "quoteCurrency": quote,
        "rate": req.rate,
        "updatedAt": now_iso,
    }
    change = _emit_change(
        db,
        user_id=current_user.id,
        sync_id=sync_id,
        action="upsert",
        payload=payload,
        device_id=device_id,
    )
    try:
        apply_user_change_to_projection(db, user_id=current_user.id, change=change)
    except Exception:
        logger.exception(
            "exchange_rate_override apply failed: base=%s quote=%s", base, quote
        )
        raise
    db.commit()
    await _broadcast_user_sync_change(request, current_user.id, change.change_id)
    return ExchangeRateOverrideWriteOut(
        sync_id=sync_id,
        base_currency=base,
        quote_currency=quote,
        rate=req.rate,
    )


@router.delete("/exchange-rate-overrides", response_model=ExchangeRateOverrideWriteOut)
async def delete_exchange_rate_override(
    base_currency: str,
    quote_currency: str,
    request: Request,
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExchangeRateOverrideWriteOut:
    base = base_currency.upper()
    quote = quote_currency.upper()
    existing = _find_pair(db, current_user.id, base, quote)
    if existing is None:
        raise HTTPException(status_code=404, detail="override not found")
    change = _emit_change(
        db,
        user_id=current_user.id,
        sync_id=existing.sync_id,
        action="delete",
        payload=None,
        device_id=device_id,
    )
    try:
        apply_user_change_to_projection(db, user_id=current_user.id, change=change)
    except Exception:
        logger.exception(
            "exchange_rate_override apply failed: base=%s quote=%s", base, quote
        )
        raise
    db.commit()
    await _broadcast_user_sync_change(request, current_user.id, change.change_id)
    return ExchangeRateOverrideWriteOut(
        sync_id=change.entity_sync_id,
        base_currency=base,
        quote_currency=quote,
    )
