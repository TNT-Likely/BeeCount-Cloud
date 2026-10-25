"""One normalization path for single/batch MCP writes, imports and recurring rules."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from ..config import get_settings
from ..models import ReadTxProjection, UserAccountProjection, UserCategoryProjection
from .attachments import resolve_transaction_attachments
from .datetime_utils import parse_transaction_datetime
from .operations import execute_operation, find_operation, save_operation
from .schemas import BatchTransactionItem, positive_amount


def dictionary_entry(db, model, owner, *, name=None, identifier=None, kind=None):
    if not name and not identifier:
        return None
    query = select(model).where(model.user_id == owner)
    query = (
        query.where(model.sync_id == identifier) if identifier else query.where(model.name == name)
    )
    if kind and model is UserCategoryProjection:
        query = query.where(model.kind == kind)
    rows = db.scalars(query.limit(2)).all()
    if not rows:
        raise ValueError(f"{model.__name__} not found: {identifier or name}")
    if len(rows) != 1:
        raise ValueError(f"Ambiguous {model.__name__}: {name!r}; use its id")
    if name and rows[0].name != name:
        raise ValueError("Name and identifier refer to different entities")
    return rows[0]


def transaction_row(db, user, sync_id, ledger_id=None):
    from .tools.read_tools import live_ledgers

    ledgers = [
        led for led in live_ledgers(db, user.id) if not ledger_id or led.external_id == ledger_id
    ]
    rows = db.scalars(
        select(ReadTxProjection)
        .where(
            ReadTxProjection.ledger_id.in_([led.id for led in ledgers]),
            ReadTxProjection.sync_id == sync_id,
        )
        .limit(2)
    ).all()
    if len(rows) != 1:
        raise ValueError("Transaction not found or identifier is ambiguous; specify ledger_id")
    return rows[0], next(led for led in ledgers if led.id == rows[0].ledger_id)


async def prepare_transaction(user, ledger, raw, *, time_zone=None, existing=None):
    from .tools import write_tools as wt

    partial = existing is not None
    kind = raw.get("tx_type") or (existing.tx_type if partial else "expense")
    if kind not in {"expense", "income", "transfer"}:
        raise ValueError("Invalid tx_type")
    body = {"base_change_id": 0}
    if not partial or raw.get("tx_type") is not None:
        body["tx_type"] = kind
    if not partial or raw.get("amount") is not None:
        body["amount"] = positive_amount(raw.get("amount"))
    if raw.get("happened_at") is not None:
        body["happened_at"] = parse_transaction_datetime(
            raw["happened_at"], time_zone=time_zone
        ).isoformat()
    elif not partial:
        body["happened_at"] = datetime.now(timezone.utc).isoformat()
    if raw.get("note") is not None:
        body["note"] = raw["note"]
    for flag in ("exclude_from_stats", "exclude_from_budget"):
        if raw.get(flag) is not None:
            body[flag] = raw[flag]
    account_currency = None
    with wt.SessionLocal() as db:
        category = dictionary_entry(
            db,
            UserCategoryProjection,
            ledger.user_id,
            name=raw.get("category"),
            identifier=raw.get("category_id"),
            kind=kind,
        )
        if category:
            body.update(
                category_id=category.sync_id, category_name=category.name, category_kind=kind
            )
        if kind == "transfer":
            from_id = raw.get("from_account_id") or raw.get("account_id")
            from_name = raw.get("from_account") or raw.get("account")
            to_id, to_name = raw.get("to_account_id"), raw.get("to_account")
            if partial and existing.tx_type == "transfer":
                if not from_id and not from_name:
                    from_id = existing.from_account_sync_id
                if not to_id and not to_name:
                    to_id = existing.to_account_sync_id
            source = dictionary_entry(
                db, UserAccountProjection, ledger.user_id, name=from_name, identifier=from_id
            )
            target = dictionary_entry(
                db, UserAccountProjection, ledger.user_id, name=to_name, identifier=to_id
            )
            if source is None or target is None:
                raise ValueError(
                    "Complete transfer requires both from_account_id/from_account and to_account_id/to_account"
                )
            if source.sync_id == target.sync_id:
                raise ValueError("Transfer accounts must be different")
            if (source.currency or ledger.currency) != (target.currency or ledger.currency):
                raise ValueError("Transfer accounts must have the same currency")
            body.update(
                from_account_id=source.sync_id,
                from_account_name=source.name,
                to_account_id=target.sync_id,
                to_account_name=target.name,
                currency_code=(source.currency or ledger.currency).upper(),
            )
        else:
            account = dictionary_entry(
                db,
                UserAccountProjection,
                ledger.user_id,
                name=raw.get("account"),
                identifier=raw.get("account_id"),
            )
            if account:
                body.update(account_id=account.sync_id, account_name=account.name)
                account_currency = account.currency
            elif partial and existing.account_sync_id:
                previous_account = db.get(
                    UserAccountProjection, (ledger.user_id, existing.account_sync_id)
                )
                account_currency = previous_account.currency if previous_account else None
        if raw.get("attachments") is not None:
            body["attachments"] = resolve_transaction_attachments(
                db, ledger_id=ledger.id, file_ids=raw["attachments"]
            )
        if not partial or raw.get("tags") is not None:
            tags = wt._merge_default_tag(raw.get("tags")) if not partial else list(raw["tags"])
            body["tags"] = tags
            body["tag_ids"] = wt._lookup_tag_sync_ids(
                db, user_id=ledger.user_id, ledger_id=ledger.id, names=tags
            )
    if kind != "transfer" and (
        not partial or any(raw.get(k) is not None for k in ("amount", "currency", "native_amount"))
    ):
        amount = body.get("amount", existing.amount if partial else None)
        currency = raw.get("currency") or (existing.currency_code if partial else None)
        if raw.get("native_amount") is not None:
            body.update(
                currency_code=(currency or account_currency or ledger.currency).upper(),
                native_amount=positive_amount(raw["native_amount"]),
            )
        elif (
            partial
            and existing.native_amount is not None
            and existing.amount
            and not raw.get("currency")
        ):
            # Preserve the transaction's historical settlement ratio on an amount correction.
            body.update(
                currency_code=existing.currency_code,
                native_amount=float(existing.native_amount) * amount / existing.amount,
            )
        else:
            body.update(
                await wt._build_currency_fields(
                    user,
                    ledger_base=ledger.currency,
                    account_currency=account_currency,
                    currency_arg=currency,
                    amount=amount,
                )
            )
    if partial and raw.get("expected_revision") is not None:
        body["expected_revision"] = raw["expected_revision"]
    return body


async def create_many(
    user, *, transactions, ledger_id=None, time_zone=None, idempotency_key=None, single=False
):
    from .tools import write_tools as wt

    if not transactions or len(transactions) > 200:
        raise ValueError("transactions must contain 1..200 entries")
    raw_request = dict(
        transactions=transactions, ledger_id=ledger_id, time_zone=time_zone, single=single
    )
    key = idempotency_key or str(uuid4())
    if not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise ValueError("idempotency_key must be a non-empty string of at most 128 characters")
    prior = find_operation(
        wt.SessionLocal,
        user.id,
        key,
        raw_request,
        kind="create_transaction" if single else "create_transactions",
    )
    with wt.SessionLocal() as db:
        led, status = wt._resolve_write_ledger(db, user, ledger_id)
        if status:
            return status
        # Keep the ledger scalar attributes accessible after the session closes.
        db.expunge(led)
    if prior:
        if prior.ledger_id != led.id:
            raise ValueError("Operation ledger no longer matches")
        outcome = await execute_operation(wt.SessionLocal, prior, user, wt._self_call)
    else:
        normalized = []
        with wt.SessionLocal() as db:
            wt._validate_names_exist(
                db,
                led.user_id,
                categories={
                    item["category"]
                    for item in transactions
                    if isinstance(item, dict) and item.get("category")
                },
                accounts={
                    item[key]
                    for item in transactions
                    if isinstance(item, dict)
                    for key in ("account", "from_account", "to_account")
                    if item.get(key)
                },
            )
        for index, raw in enumerate(transactions):
            try:
                item = BatchTransactionItem.model_validate(raw).model_dump()
                normalized.append(await prepare_transaction(user, led, item, time_zone=time_zone))
            except ValueError as exc:
                raise ValueError(f"transactions[{index}]: {exc}") from exc
        with wt.SessionLocal() as db:
            missing_tag = wt._is_tag_missing_in_ledger(
                db, user_id=led.user_id, ledger_id=led.id, tag_name=wt._MCP_DEFAULT_TAG
            )
        if led.user_id == user.id and missing_tag:
            await wt._ensure_mcp_tag(user, led.external_id)
        if led.user_id == user.id:
            # Resolve tag IDs after creating the default tag, before freezing the operation.
            with wt.SessionLocal() as db:
                for body in normalized:
                    body["tag_ids"] = wt._lookup_tag_sync_ids(
                        db, user_id=led.user_id, ledger_id=led.id, names=body.get("tags") or []
                    )
        root = f"{get_settings().api_prefix}/write/ledgers/{led.external_id}/transactions"
        if single:
            steps = [dict(method="POST", path=root, body=normalized[0])]
        else:
            items = [
                {k: v for k, v in body.items() if k != "base_change_id"} for body in normalized
            ]
            steps = [
                dict(
                    method="POST",
                    path=root + "/batch",
                    body=dict(
                        base_change_id=0, transactions=items[start : start + 50], auto_ai_tag=False
                    ),
                )
                for start in range(0, len(items), 50)
            ]
        row = save_operation(
            wt.SessionLocal,
            user_id=user.id,
            ledger_id=led.id,
            key=key,
            kind="create_transaction" if single else "create_transactions",
            request=raw_request,
            steps=steps,
        )
        outcome = await execute_operation(wt.SessionLocal, row, user, wt._self_call)
    outcome.update(
        ledger=led.name, ledger_id=led.external_id, created_count=len(outcome["sync_ids"])
    )
    if outcome["status"] == "completed":
        outcome["status"] = "created"
    if single and outcome["sync_ids"]:
        with wt.SessionLocal() as db:
            tx = db.get(ReadTxProjection, (led.id, outcome["sync_ids"][0]))
            outcome.update(
                sync_id=tx.sync_id,
                amount=tx.amount,
                tx_type=tx.tx_type,
                happened_at=tx.happened_at.replace(tzinfo=timezone.utc).isoformat(),
            )
            if transactions[0].get("attachments") is not None:
                import json

                outcome["attachments"] = json.loads(tx.attachments_json or "[]")
    return outcome


async def update_one(user, *, sync_id, ledger_id=None, **raw):
    from .tools import write_tools as wt

    with wt.SessionLocal() as db:
        existing, led = transaction_row(db, user, sync_id, ledger_id)
        db.expunge(existing)
        db.expunge(led)
    body = await prepare_transaction(
        user, led, raw, time_zone=raw.get("time_zone"), existing=existing
    )
    path = f"{get_settings().api_prefix}/write/ledgers/{led.external_id}/transactions/{sync_id}"
    result = await wt._self_call("PATCH", path, user, json=body)
    return dict(
        sync_id=sync_id,
        ledger_id=led.external_id,
        updated=[key for key in body if key not in {"base_change_id", "expected_revision"}],
        **({"attachments": body["attachments"]} if "attachments" in body else {}),
        _meta=result,
    )
