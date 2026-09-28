"""股票交易明細 write endpoints(2026-09-28,docs/STOCK_HOLDINGS_SD.md)。

POST / PATCH / DELETE for /ledgers/{ledger_id}/stock-trades。buy/sell 在同一次
`_commit_write` 裡連帶建立/更新/刪除綁定的轉帳交易(見
`snapshot_mutator.create_stock_trade`),App 透過 sync pull 一次拿到兩筆變更。

Phase 2 股利:
- cash_dividend/reinvest 也能手動建(income 交易,分類固定「股利」)。
- `POST /securities/pending-dividends/{id}/confirm|dismiss|restore`:待確認股利。
  確認時由 server 建 cash_dividend 或 reinvest(+ 配股 stock_dividend)明細與
  income 交易,App/Web 共用(App 確認後 sync pull 拿回結果)。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol
from ...models import (
    PendingDividend,
    SecurityDividendEvent,
    UserAccountProjection,
    UserExchangeRateProjection,
)
from ...schemas import PendingDividendConfirmRequest
from ...services import card_rewards
from ...services.securities import dividends as dividend_service
from ...services.securities import holdings as holdings_service
from ...services.securities import markets
from ...snapshot_mutator import (
    DIVIDEND_CATEGORY_ID_KEY,
    DIVIDEND_CATEGORY_NAME_KEY,
    STOCK_TRADE_INCOME_TYPES,
    TX_FX_CURRENCY_KEY,
    TX_FX_RATE_KEY,
    create_stock_trade,
    delete_stock_trade,
    update_stock_trade,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_SHARES_EPS = 1e-6


def _held_shares(
    db: Session,
    *,
    user_id: str,
    account_id: str,
    market: str,
    symbol: str,
    exclude_trade_id: str | None = None,
) -> float:
    """跨該使用者所有帳本彙總(帳戶是 user-global,同一個投資理財帳戶可能在
    不同帳本都有交易)。"""
    stmt = select(ReadStockTradeProjection).where(
        ReadStockTradeProjection.user_id == user_id,
        ReadStockTradeProjection.account_sync_id == account_id,
        ReadStockTradeProjection.market == market.upper(),
        ReadStockTradeProjection.symbol == symbol.upper(),
    )
    rows = [
        holdings_service.TradeRow(
            sync_id=t.sync_id, account_id=t.account_sync_id, market=t.market, symbol=t.symbol,
            trade_type=t.trade_type, shares=float(t.shares or 0), price=t.price,
            fee=float(t.fee or 0), tax=float(t.tax or 0), amount=float(t.amount or 0),
            trade_date=t.trade_date,
        )
        for t in db.scalars(stmt).all()
        if t.sync_id != exclude_trade_id
    ]
    return holdings_service.held_shares(rows, account_id=account_id, market=market, symbol=symbol)


def _assert_can_sell(db: Session, *, user_id: str, account_id: str, market: str, symbol: str,
                     shares: float, exclude_trade_id: str | None = None) -> None:
    held = _held_shares(
        db, user_id=user_id, account_id=account_id, market=market, symbol=symbol,
        exclude_trade_id=exclude_trade_id,
    )
    if shares > held + _SHARES_EPS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"cannot sell {shares:g} shares, only {held:g} held",
        )


def _account_projection(db: Session, *, user_id: str, account_id: str | None) -> UserAccountProjection | None:
    if not account_id:
        return None
    return db.scalar(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.sync_id == account_id,
        )
    )


async def _dividend_tx_extras(
    db: Session, *, ledger: Ledger, receiving_account_id: str | None,
) -> dict:
    """股利 income 交易要的額外 payload:固定的「股利」分類 + 入帳帳戶幣別不是
    帳本本位幣時的折算匯率(1 單位帳戶幣別 = ? 本位幣)。"""
    extras: dict[str, object] = {
        DIVIDEND_CATEGORY_ID_KEY: card_rewards.ensure_dividend_category(db, user_id=ledger.user_id),
        DIVIDEND_CATEGORY_NAME_KEY: card_rewards.DIVIDEND_CATEGORY_NAME,
    }
    account = _account_projection(db, user_id=ledger.user_id, account_id=receiving_account_id)
    base = (ledger.currency or "").strip().upper()
    currency = ((account.currency if account else None) or base).strip().upper()
    if currency and base and currency != base:
        rate = await _fx_rate_to_base(db, user_id=ledger.user_id, base=base, currency=currency)
        extras[TX_FX_CURRENCY_KEY] = currency
        # 缺匯率退化 1:1(currencyCode 仍落,App L11「重新計算外幣折算」可事後
        # 捞回;同 mcp write_tools._build_currency_fields 的慣例)。
        extras[TX_FX_RATE_KEY] = rate or 1.0
    return extras


async def _fx_rate_to_base(db: Session, *, user_id: str, base: str, currency: str) -> float | None:
    """1 單位 currency = ? base。手動 override(1 quote = rate base)優先,
    其次自動匯率源(1 base = x quote,取倒數)。"""
    override = db.scalar(
        select(UserExchangeRateProjection.rate).where(
            UserExchangeRateProjection.user_id == user_id,
            UserExchangeRateProjection.base_currency == base,
            UserExchangeRateProjection.quote_currency == currency,
        ).limit(1)
    )
    try:
        if override is not None and float(override) > 0:
            return float(override)
    except (TypeError, ValueError):
        pass
    try:
        from ...services.exchange_rate import fetcher

        row, _stale = await fetcher.get_rates(db, base)
        raw = dict(row.payload_json).get(currency) or dict(row.payload_json).get(currency.lower())
        x = float(raw) if raw is not None else 0.0
        return 1.0 / x if x > 0 else None
    except Exception:  # noqa: BLE001 — 匯率上游掛了不能擋住入帳
        logger.warning("dividend: exchange rate lookup failed base=%s quote=%s", base, currency)
        return None


@router.post(
    "/ledgers/{ledger_id}/stock-trades",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_stock_trade_api(
    ledger_id: str,
    req: WriteStockTradeCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json")
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_TRANSACTION_WRITE_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    if req.trade_type == "sell":
        _assert_can_sell(
            db, user_id=ledger.user_id, account_id=req.account_id, market=req.market,
            symbol=req.symbol, shares=req.shares,
        )
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    if req.trade_type in STOCK_TRADE_INCOME_TYPES:
        mutate_payload.update(await _dividend_tx_extras(
            db, ledger=ledger,
            receiving_account_id=req.account_id if req.trade_type == "reinvest" else req.settlement_account_id,
        ))
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_stock_trade_create",
        mutate=lambda snapshot: create_stock_trade(snapshot, mutate_payload),
    )


@router.patch(
    "/ledgers/{ledger_id}/stock-trades/{trade_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_stock_trade_api(
    ledger_id: str,
    trade_id: str,
    req: WriteStockTradeUpdateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json", exclude_unset=True)
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_TRANSACTION_WRITE_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    existing = db.scalar(
        select(ReadStockTradeProjection).where(
            ReadStockTradeProjection.ledger_id == ledger.id,
            ReadStockTradeProjection.sync_id == trade_id,
        )
    )
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    if existing.trade_type == "sell" and payload.get("shares") is not None:
        _assert_can_sell(
            db, user_id=ledger.user_id, account_id=existing.account_sync_id or "",
            market=existing.market, symbol=existing.symbol, shares=float(payload["shares"]),
            exclude_trade_id=trade_id,
        )
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_stock_trade_update",
        mutate=lambda snapshot: (update_stock_trade(snapshot, trade_id, mutate_payload), trade_id),
    )


@router.delete(
    "/ledgers/{ledger_id}/stock-trades/{trade_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_stock_trade_api(
    ledger_id: str,
    trade_id: str,
    req: WriteEntityDeleteRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json")
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_TRANSACTION_WRITE_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_stock_trade_delete",
        mutate=lambda snapshot: (delete_stock_trade(snapshot, trade_id, mutate_payload), trade_id),
    )


# ---------------------------------------------------------------------------
# 待確認股利(Phase 2)
# ---------------------------------------------------------------------------


def _load_pending(db: Session, *, pending_id: int, user_id: str) -> PendingDividend:
    pending = db.get(PendingDividend, pending_id)
    if pending is None or pending.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending dividend not found")
    return pending


def _default_trade_date(event: SecurityDividendEvent, market_code: str) -> datetime:
    """沒給入帳日期時:發放日(有的話)否則除息日,當地中午(跟 Web/App 的
    「日期存當地中午」同慣例,換 UTC 不會跨日)。"""
    m = markets.get_market(market_code)
    day = event.pay_date or event.ex_date
    tz = m.zone if m else timezone.utc
    return datetime.combine(day, time(12, 0), tzinfo=tz).astimezone(timezone.utc)


def build_confirm_trades(
    pending: PendingDividend, event: SecurityDividendEvent, req: PendingDividendConfirmRequest,
    *, settlement_account_id: str | None,
) -> list[dict]:
    """把確認請求轉成要建的 stock_trade payload(create_stock_trade 的格式)。"""
    ref = dividend_service.event_ref(pending.market, pending.symbol, event.ex_date)
    trade_date = (req.trade_date or _default_trade_date(event, pending.market)).isoformat()
    common = {
        "account_id": pending.account_sync_id,
        "market": pending.market,
        "symbol": pending.symbol,
        "security_name": pending.security_name,
        "currency": pending.currency,
        "trade_date": trade_date,
        "dividend_event_ref": ref,
        "note": req.note,
    }
    trades: list[dict] = []
    cash_per_share = req.cash_per_share if req.cash_per_share is not None else event.cash_per_share
    if cash_per_share > 0 and pending.shares > 0:
        if req.mode == "reinvest":
            if req.reinvest_shares is None or req.reinvest_price is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="reinvest_shares and reinvest_price are required for reinvest",
                )
            trades.append({
                **common, "trade_type": "reinvest", "shares": req.reinvest_shares,
                "price": req.reinvest_price, "fee": req.reinvest_fee or 0.0, "tax": 0.0,
                "settlement_amount": req.settlement_amount,
            })
        else:
            if not settlement_account_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="settlement_account_id is required to receive a cash dividend",
                )
            trades.append({
                **common, "trade_type": "cash_dividend", "shares": pending.shares,
                "price": cash_per_share,
                "fee": req.fee if req.fee is not None else pending.est_fee,
                "tax": req.tax if req.tax is not None else pending.est_tax,
                "settlement_account_id": settlement_account_id,
                "settlement_amount": req.settlement_amount,
            })
    stock_shares = req.stock_shares if req.stock_shares is not None else pending.est_stock_shares
    if stock_shares > 0:
        trades.append({
            **common, "trade_type": "stock_dividend", "shares": stock_shares, "price": 0.0,
            "fee": 0.0, "tax": 0.0, "note": None,
        })
    if not trades:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="nothing to record")
    return trades


@router.post(
    "/securities/pending-dividends/{pending_id}/confirm",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def confirm_pending_dividend_api(
    pending_id: int,
    req: PendingDividendConfirmRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    pending = _load_pending(db, pending_id=pending_id, user_id=current_user.id)
    if pending.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"dividend already {pending.status}")
    event = db.get(SecurityDividendEvent, pending.event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dividend event not found")
    ledger_external_id = db.scalar(select(Ledger.external_id).where(Ledger.id == pending.ledger_id))
    if not ledger_external_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger not found")
    payload = req.model_dump(mode="json")
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_external_id,
        required_roles=_TRANSACTION_WRITE_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay

    account = _account_projection(db, user_id=pending.user_id, account_id=pending.account_sync_id)
    settings = dividend_service.parse_settings(account.investment_settings_json if account else None)
    settlement_id = req.settlement_account_id or dividend_service.default_receiving_account(
        db, user_id=pending.user_id, account_id=pending.account_sync_id, settings=settings,
    )
    trade_payloads = build_confirm_trades(pending, event, req, settlement_account_id=settlement_id)
    extras: dict = {}
    if any(t["trade_type"] in STOCK_TRADE_INCOME_TYPES for t in trade_payloads):
        extras = await _dividend_tx_extras(
            db, ledger=ledger,
            receiving_account_id=pending.account_sync_id if req.mode == "reinvest" else settlement_id,
        )
    actor_payload = _payload_with_actor({}, current_user, ledger=ledger)

    def _mutate(snapshot: dict) -> tuple[dict, str | None]:
        target = snapshot
        created: list[str] = []
        for trade_payload in trade_payloads:
            target, trade_id = create_stock_trade(target, {**trade_payload, **actor_payload, **extras})
            created.append(trade_id)
        pending.status = "confirmed"
        pending.resolved_at = datetime.now(timezone.utc)
        pending.created_trade_ids = json.dumps(created)
        return target, created[0]

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_dividend_confirm",
        mutate=_mutate,
    )


def _set_pending_status(db: Session, *, pending_id: int, user_id: str, expect: str, to: str) -> dict:
    pending = _load_pending(db, pending_id=pending_id, user_id=user_id)
    if pending.status != expect:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"dividend is {pending.status}")
    pending.status = to
    pending.resolved_at = datetime.now(timezone.utc) if to != "pending" else None
    db.commit()
    return {"id": pending.id, "status": pending.status}


@router.post("/securities/pending-dividends/{pending_id}/dismiss")
def dismiss_pending_dividend_api(
    pending_id: int,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """忽略(例:已經賣掉沒領到、券商資料不同)。不建任何交易。"""
    return _set_pending_status(db, pending_id=pending_id, user_id=current_user.id, expect="pending", to="dismissed")


@router.post("/securities/pending-dividends/{pending_id}/restore")
def restore_pending_dividend_api(
    pending_id: int,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """把已忽略的股利放回待確認。"""
    return _set_pending_status(db, pending_id=pending_id, user_id=current_user.id, expect="dismissed", to="pending")
