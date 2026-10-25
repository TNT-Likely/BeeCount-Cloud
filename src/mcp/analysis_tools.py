"""Composable, explainable aggregation with exact IANA calendar boundaries."""

from __future__ import annotations

import json
from datetime import timezone
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import or_, select

from ..models import ReadTxProjection, User, UserAccountProjection
from .capability_tools import Fields, require_ledger
from .query_context import date_boundary, revision, zone
from .tools import read_tools as rt


class TransactionFilter(Fields):
    date_from: str | None = None
    date_to: str | None = None
    tx_type: Literal["income", "expense", "transfer"] | None = None
    category_ids: list[str] = Field(default_factory=list, max_length=100)
    account_ids: list[str] = Field(default_factory=list, max_length=100)
    tag_ids: list[str] = Field(default_factory=list, max_length=100)
    member_user_ids: list[str] = Field(default_factory=list, max_length=100)
    note_contains: str | None = None
    min_amount: float | None = Field(default=None, ge=0)
    max_amount: float | None = Field(default=None, ge=0)
    include_excluded: bool = False


def filtered_query(ledger, filters, time_zone):
    query = select(ReadTxProjection).where(ReadTxProjection.ledger_id == ledger.id)
    if filters.date_from:
        query = query.where(
            ReadTxProjection.happened_at >= date_boundary(filters.date_from, time_zone=time_zone)
        )
    if filters.date_to:
        query = query.where(
            ReadTxProjection.happened_at
            < date_boundary(filters.date_to, end=True, time_zone=time_zone)
        )
    if filters.tx_type:
        query = query.where(ReadTxProjection.tx_type == filters.tx_type)
    if filters.category_ids:
        query = query.where(ReadTxProjection.category_sync_id.in_(filters.category_ids))
    if filters.account_ids:
        query = query.where(
            or_(
                ReadTxProjection.account_sync_id.in_(filters.account_ids),
                ReadTxProjection.from_account_sync_id.in_(filters.account_ids),
                ReadTxProjection.to_account_sync_id.in_(filters.account_ids),
            )
        )
    if filters.member_user_ids:
        query = query.where(ReadTxProjection.created_by_user_id.in_(filters.member_user_ids))
    if filters.note_contains:
        escaped = (
            filters.note_contains.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        query = query.where(ReadTxProjection.note.ilike("%" + escaped + "%", escape="\\"))
    if filters.min_amount is not None:
        query = query.where(ReadTxProjection.amount >= filters.min_amount)
    if filters.max_amount is not None:
        query = query.where(ReadTxProjection.amount <= filters.max_amount)
    if not filters.include_excluded:
        query = query.where(ReadTxProjection.exclude_from_stats.is_(False))
    return query


def analyze_transactions(
    user: User,
    ledger_id: str,
    filters: TransactionFilter | None = None,
    group_by: Literal[
        "none", "day", "week", "month", "category", "account", "tag", "member", "type"
    ] = "month",
    time_zone: str | None = None,
    comparison: TransactionFilter | None = None,
    top_n: int = 100,
) -> dict[str, Any]:
    """Aggregate matching transactions by local calendar/category/account/tag/member with optional comparison filters and exact drill-down context. Income/expense use ledger settlement currency; transfers are separate and never counted as consumption. Multi-tag groups overlap."""
    filters = filters or TransactionFilter()
    if (
        filters.min_amount is not None
        and filters.max_amount is not None
        and filters.min_amount > filters.max_amount
    ):
        raise ValueError("Invalid amount range")
    tz = zone(time_zone)
    with rt.SessionLocal() as db:
        ledger = require_ledger(db, user, ledger_id)
        baseline = revision(db, [ledger.id], ledger.user_id)
        account_currencies = dict(
            db.execute(
                select(UserAccountProjection.sync_id, UserAccountProjection.currency).where(
                    UserAccountProjection.user_id == ledger.user_id
                )
            ).all()
        )

        def collect(criteria):
            query = filtered_query(ledger, criteria, str(tz))
            rows = db.scalars(
                query.order_by(ReadTxProjection.happened_at, ReadTxProjection.sync_id)
            ).all()
            if criteria.tag_ids:
                rows = [
                    row
                    for row in rows
                    if set(criteria.tag_ids).issubset(
                        set(json.loads(row.tag_sync_ids_json or "[]"))
                    )
                ]
            totals = dict(
                income=0.0,
                expense=0.0,
                transfer=0.0,
                transaction_count=len(rows),
                transfer_by_currency={},
            )
            groups = {}
            first, last = None, None
            for row in rows:
                amount = float(
                    row.native_amount if row.native_amount is not None else row.amount or 0
                )
                transfer_currency = (
                    row.currency_code
                    or account_currencies.get(row.from_account_sync_id)
                    or ledger.currency
                )
                if row.tx_type == "transfer":
                    totals["transfer_by_currency"][transfer_currency] = totals[
                        "transfer_by_currency"
                    ].get(transfer_currency, 0) + float(row.amount or 0)
                    if transfer_currency != ledger.currency and row.native_amount is None:
                        amount = 0.0
                if row.tx_type in totals:
                    totals[row.tx_type] += amount
                local = row.happened_at.replace(tzinfo=timezone.utc).astimezone(tz)
                first, last = first or local.isoformat(), local.isoformat()
                if group_by == "day":
                    keys = [local.strftime("%Y-%m-%d")]
                elif group_by == "week":
                    keys = [local.strftime("%G-W%V")]
                elif group_by == "month":
                    keys = [local.strftime("%Y-%m")]
                elif group_by == "category":
                    keys = [row.category_sync_id or "(uncategorized)"]
                elif group_by == "account":
                    keys = [row.account_sync_id or row.from_account_sync_id or "(unassigned)"]
                elif group_by == "tag":
                    keys = json.loads(row.tag_sync_ids_json or "[]") or ["(untagged)"]
                elif group_by == "member":
                    keys = [row.created_by_user_id or ledger.user_id]
                elif group_by == "type":
                    keys = [row.tx_type]
                else:
                    keys = ["all"]
                for key in keys:
                    slot = groups.setdefault(
                        key,
                        dict(
                            key=key,
                            income=0.0,
                            expense=0.0,
                            transfer=0.0,
                            transfer_by_currency={},
                            transaction_count=0,
                            sample_sync_ids=[],
                            category_name=row.category_name if group_by == "category" else None,
                        ),
                    )
                    slot["transaction_count"] += 1
                    if row.tx_type == "transfer":
                        slot["transfer_by_currency"][transfer_currency] = slot[
                            "transfer_by_currency"
                        ].get(transfer_currency, 0) + float(row.amount or 0)
                    if row.tx_type in {"income", "expense", "transfer"}:
                        slot[row.tx_type] += amount
                    if len(slot["sample_sync_ids"]) < 10:
                        slot["sample_sync_ids"].append(row.sync_id)
            totals["balance"] = totals["income"] - totals["expense"]
            values = list(groups.values())
            if group_by in {"day", "week", "month"}:
                values.sort(key=lambda item: item["key"])
            else:
                values.sort(key=lambda item: (-item["expense"], item["key"]))
            return dict(
                totals=totals,
                groups=values[: max(1, min(top_n, 500))],
                total_groups=len(groups),
                groups_truncated=len(values) > max(1, min(top_n, 500)),
                recorded_coverage=dict(
                    first_transaction=first,
                    last_transaction=last,
                    completeness="Only recorded data; source/checkpoint coverage is separate",
                ),
            )

        result = collect(filters)
        if comparison:
            other = collect(comparison)
            result["comparison"] = other
            result["difference"] = {
                key: result["totals"][key] - other["totals"][key]
                for key in ["income", "expense", "balance", "transfer", "transaction_count"]
            }
        if revision(db, [ledger.id], ledger.user_id) != baseline:
            raise ValueError("REVISION_CONFLICT: data changed during analysis; retry")
        return {
            **result,
            "ledger_id": ledger_id,
            "currency": ledger.currency,
            "revision": baseline,
            "filters": filters.model_dump(mode="json"),
            "time_zone": str(tz),
            "group_by": group_by,
            "calculation": dict(
                amount="native_amount or amount in ledger base currency",
                transfer="excluded from income/expense; transfer numeric subtotal includes known base-currency amounts only, transfer_by_currency preserves every original amount",
                refunds="no automatic pairing or netting across months",
                tag_groups="overlap when a transaction has several tags",
                date_to="local dates inclusive; timestamp boundaries exclusive",
            ),
            "drill_down": dict(
                tool="list_transactions",
                ledger_id=ledger_id,
                date_from=filters.date_from,
                date_to=filters.date_to,
                time_zone=str(tz),
                revision=baseline,
            ),
        }


READ_FUNCTIONS = [analyze_transactions]
WRITE_FUNCTIONS = []
