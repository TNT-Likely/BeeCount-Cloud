"""Account history in original currencies with explicit conversion coverage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..models import (
    ExchangeRateCache,
    ReadTxProjection,
    UserAccountProjection,
    UserExchangeRateProjection,
    UserProfile,
)
from .query_context import revision, zone
from .tools import read_tools as rt


def history(user, *, ledger_id=None, time_zone=None, tz_offset_minutes=0, base_currency=None):
    from .capability_tools import require_ledger

    if not -720 <= tz_offset_minutes <= 840:
        raise ValueError("Invalid timezone offset")
    tz = (
        zone(time_zone)
        if time_zone or not tz_offset_minutes
        else timezone(timedelta(minutes=tz_offset_minutes))
    )
    with rt.SessionLocal() as db:
        ledgers = (
            [require_ledger(db, user, ledger_id)]
            if ledger_id
            else [led for led in rt.live_ledgers(db, user.id) if led.user_id == user.id]
        )
        owners = {led.user_id for led in ledgers} or {user.id}
        accounts = db.scalars(
            select(UserAccountProjection).where(UserAccountProjection.user_id.in_(owners))
        ).all()
        account_map = {(acct.user_id, acct.sync_id): acct for acct in accounts}
        balances = {key: float(acct.initial_balance or 0) for key, acct in account_map.items()}
        currencies = {acct.currency or "CNY" for acct in accounts}
        baseline_owner = next(iter(owners)) if len(owners) == 1 else user.id
        baseline = revision(db, [led.id for led in ledgers], baseline_owner)
        ledger_owners = {led.id: led.user_id for led in ledgers}
        txs = db.scalars(
            select(ReadTxProjection)
            .where(ReadTxProjection.ledger_id.in_(list(ledger_owners)))
            .order_by(ReadTxProjection.happened_at, ReadTxProjection.sync_id)
        ).all()
        owner = next(iter(owners)) if len(owners) == 1 else user.id
        base = (
            base_currency
            or db.scalar(select(UserProfile.primary_currency).where(UserProfile.user_id == owner))
            or (next(iter(currencies)) if len(currencies) == 1 else "")
        ).upper()
        rates = {base: 1.0} if base else {}
        cached = db.scalar(
            select(ExchangeRateCache.payload_json).where(ExchangeRateCache.base_currency == base)
        )
        if isinstance(cached, dict):
            for currency, raw in cached.items():
                try:
                    rate = float(raw)
                    if rate > 0:
                        rates[currency.upper()] = 1.0 / rate
                except (TypeError, ValueError):
                    continue
        for override in db.scalars(
            select(UserExchangeRateProjection).where(
                UserExchangeRateProjection.user_id == owner,
                UserExchangeRateProjection.base_currency == base,
            )
        ).all():
            try:
                rate = float(override.rate)
                if rate > 0:
                    rates[override.quote_currency.upper()] = rate
            except (TypeError, ValueError):
                continue
        raw_series = []

        def snapshot(bucket):
            totals = {}
            for key, balance in balances.items():
                account = account_map[key]
                currency = account.currency or "CNY"
                slot = totals.setdefault(currency, dict(assets=0.0, liabilities=0.0, net_worth=0.0))
                if account.account_type in {"credit_card", "loan"}:
                    slot["assets"] += max(0.0, balance)
                    slot["liabilities"] += max(0.0, -balance)
                else:
                    slot["assets"] += balance
                slot["net_worth"] = slot["assets"] - slot["liabilities"]
            raw_series.append(dict(bucket=bucket, by_currency=totals))

        bucket = None
        for tx in txs:
            current = tx.happened_at.replace(tzinfo=timezone.utc).astimezone(tz).strftime("%Y-%m")
            if bucket and current != bucket:
                snapshot(bucket)
            owner = ledger_owners[tx.ledger_id]
            amount = float(tx.amount or 0)
            effects = (
                [(tx.from_account_sync_id, -amount), (tx.to_account_sync_id, amount)]
                if tx.tx_type == "transfer"
                else [(tx.account_sync_id, amount if tx.tx_type == "income" else -amount)]
            )
            for aid, delta in effects:
                if (owner, aid) in balances:
                    balances[owner, aid] += delta
            bucket = current
        snapshot(bucket or datetime.now(timezone.utc).astimezone(tz).strftime("%Y-%m"))
        # Fill missing calendar months with the preceding balance (a stock, not a cash-flow sum).
        sparse = {item["bucket"]: item for item in raw_series}
        first = min(sparse)
        last = max(max(sparse), datetime.now(timezone.utc).astimezone(tz).strftime("%Y-%m"))
        y, m = map(int, first.split("-"))
        filled, previous = [], None
        while f"{y:04d}-{m:02d}" <= last:
            key = f"{y:04d}-{m:02d}"
            if key in sparse:
                previous = sparse[key]["by_currency"]
            filled.append(dict(bucket=key, by_currency=previous or {}))
            m += 1
            if m == 13:
                y, m = y + 1, 1
        missing = sorted(currency for currency in currencies if currency not in rates)
        converted = []
        # Never publish an incomplete cross-currency total as net worth.
        if base and not missing:
            for item in filled:
                totals = dict(assets=0.0, liabilities=0.0, net_worth=0.0)
                for currency, values in item["by_currency"].items():
                    for metric in totals:
                        totals[metric] += values[metric] * rates[currency]
                converted.append(dict(bucket=item["bucket"], **totals))
        if revision(db, list(ledger_owners), baseline_owner) != baseline:
            raise ValueError("REVISION_CONFLICT: data changed during history calculation")
        return dict(
            series=converted,
            series_by_currency=filled,
            currency=base or None,
            multi_currency=len(currencies) > 1,
            missing_conversion_currencies=missing,
            conversion_complete=bool(base) and not missing,
            revision=baseline,
            conversion_policy="Original-currency balance history; current configured/cached rates for optional valuation, not historical FX returns",
            coverage=dict(
                ledger_ids=[led.external_id for led in ledgers],
                partial=ledger_id is not None,
                opening_balance_effective_date="Legacy opening balances are undated",
            ),
            liability_convention="Positive debt amounts; net_worth = assets - liabilities",
        )
