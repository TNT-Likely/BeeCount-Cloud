"""MCP read tools — 11 个,LLM 用来查询用户数据。

每个 tool 都:
  1. require_mcp_scope(ctx, "mcp:read")
  2. 直接用 SQLAlchemy 查 read_*_projection 表(BeeCount 的读侧物化视图)
  3. 返回 dict / list[dict],MCP SDK 自动序列化成 LLM 能消费的 JSON

不直接调现有 FastAPI router(避免 HTTP self-call 和 dep tree 复杂度)。
查询逻辑跟 routers/read/* 同模式,但参数更友好(LLM 不擅长 UUID,用 name
模糊查更好)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ...database import SessionLocal
from ...models import (
    Ledger,
    ReadBudgetProjection,
    ReadTxProjection,
    User,
    UserAccountProjection,
    UserCategoryProjection,
    UserTagProjection,
)
from ...routers.read._shared import _is_ledger_deleted
from ..query_context import date_boundary, decode_cursor, encode_cursor, fingerprint, revision, visible_ledgers

# 复用 read 端的唯一权威"软删除"判定 —— 保证 MCP 与 web/mobile 账本可见性口径
# 一致(issue #31)。read._shared 不依赖 mcp,无循环 import。


# ---------- helpers ----------------------------------------------------------


def live_ledgers(db: Session, user_id: str) -> list[Ledger]:
    """该用户所有**未软删**账本,按 created_at 升序。

    issue #31:MCP 历史上直接查 Ledger 表、不过滤软删账本,导致解析 / 列举到
    web 与 mobile 都看不见的"幽灵账本"(尤其多设备各自上传的默认账本)。这里
    统一复用 read 端的 `_is_ledger_deleted` 权威判定,跟账本列表口径对齐。
    """
    return visible_ledgers(db, user_id)


def _resolve_ledger(
    db: Session, user_id: str, ledger_id: str | None
) -> Ledger | None:
    """LLM 友好的 ledger 解析(已排除软删账本,issue #31):
      - ledger_id 是 external_id(用户视角的 id,如 "1" / UUID)→ 按 external_id 查;
        命中软删账本 / 不存在 → None
      - 空 → 第一个**未软删**账本(按 created_at 升序)
    """
    if ledger_id:
        matches = [led for led in live_ledgers(db, user_id) if led.external_id == ledger_id]
        if len(matches) > 1:
            raise ValueError("Ambiguous ledger identifier")
        return matches[0] if matches else None
    live = live_ledgers(db, user_id)
    return live[0] if live else None


def _serialize_tx(row: ReadTxProjection, category_name: str | None) -> dict[str, Any]:
    # SQLite 丢失 tzinfo，但存储值已是 UTC；返回时补回，避免 Agent 再次误判。
    happened = row.happened_at
    if happened is not None:
        happened = (
            happened.replace(tzinfo=timezone.utc) if happened.tzinfo is None
            else happened.astimezone(timezone.utc)
        )
    return {
        "sync_id": row.sync_id,
        "revision": row.source_change_id,
        "tx_type": row.tx_type,
        "amount": float(row.amount or 0),
        "happened_at": happened.isoformat() if happened else None,
        "note": row.note,
        "category_name": category_name or row.category_name,
        "category_id": row.category_sync_id,
        "account_name": row.account_name,
        "account_id": row.account_sync_id,
        "from_account_name": row.from_account_name,
        "from_account_id": row.from_account_sync_id,
        "to_account_name": row.to_account_name,
        "to_account_id": row.to_account_sync_id,
        "tags": row.tags_csv or "",
        "tag_ids": json.loads(row.tag_sync_ids_json or "[]"),
        "currency_code": row.currency_code,
        "native_amount": row.native_amount,
        "exclude_from_stats": bool(row.exclude_from_stats),
        "exclude_from_budget": bool(row.exclude_from_budget),
        "created_by_user_id": row.created_by_user_id,
        "last_edited_by_user_id": row.last_edited_by_user_id,
    }


# ---------- tool implementations --------------------------------------------


def list_ledgers(user: User) -> list[dict[str, Any]]:
    """列出当前用户的所有账本(已排除软删账本,issue #31)。

    返回 external_id / name / currency / created_at。
    """
    with SessionLocal() as db:
        return [
            {
                "id": r.external_id,
                "name": r.name,
                "currency": r.currency,
                "month_start_day": r.month_start_day,
                "owner_user_id": r.user_id,
                "shared": r.user_id != user.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in live_ledgers(db, user.id)
        ]


def get_active_ledger(user: User) -> dict[str, Any] | None:
    """拿用户的"首选账本" — 当前实现为时间最早创建的那一个(后续可扩展为
    用户上一次切换记忆)。LLM 没指定 ledger_id 时用这个 fallback。
    """
    with SessionLocal() as db:
        led = _resolve_ledger(db, user.id, None)
        if led is None:
            return None
        return {
            "id": led.external_id,
            "name": led.name,
            "currency": led.currency,
        }


def list_transactions(
    user: User,
    *,
    ledger_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    account: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    q: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    tx_type: str | None = None,
    category_id: str | None = None,
    account_id: str | None = None,
    tag_id: str | None = None,
    time_zone: str | None = None,
) -> dict[str, Any]:
    """查询交易,支持多维筛选。

    LLM 友好参数:
      - date_from / date_to: ISO 日期(YYYY-MM-DD) 或 datetime
      - category / account: 名字(精确匹配,跟 LLM 提取出来的人类可读名对齐)
      - q: 全文模糊搜备注
    """
    with SessionLocal() as db:
        led = _resolve_ledger(db, user.id, ledger_id)
        if led is None:
            return {"items": [], "total": 0}

        filters = dict(ledger=led.id, user=user.id, date_from=date_from, date_to=date_to,
                       category=category, account=account, min_amount=min_amount,
                       max_amount=max_amount, q=q, tx_type=tx_type, category_id=category_id,
                       account_id=account_id, tag_id=tag_id, time_zone=time_zone)
        signature = fingerprint(filters)
        current_revision = revision(db, [led.id], led.user_id)
        continuation = decode_cursor(cursor) if cursor else None
        if continuation and (continuation.get("filter") != signature
                             or continuation.get("revision") != current_revision):
            raise ValueError("CURSOR_INVALIDATED: filters, permissions or data changed; restart the query")
        query = select(ReadTxProjection).where(ReadTxProjection.ledger_id == led.id)
        if tx_type:
            if tx_type not in {"income", "expense", "transfer"}:
                raise ValueError("Invalid tx_type")
            query = query.where(ReadTxProjection.tx_type == tx_type)
        if category_id:
            query = query.where(ReadTxProjection.category_sync_id == category_id)
        if account_id:
            query = query.where(or_(ReadTxProjection.account_sync_id == account_id,
                                   ReadTxProjection.from_account_sync_id == account_id,
                                   ReadTxProjection.to_account_sync_id == account_id))
        if tag_id:
            query = query.where(ReadTxProjection.tag_sync_ids_json.like(
                '%"' + tag_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + '"%', escape="\\"))
        if date_from:
            query = query.where(ReadTxProjection.happened_at >= date_boundary(date_from, time_zone=time_zone))
        if date_to:
            query = query.where(ReadTxProjection.happened_at < date_boundary(date_to, end=True, time_zone=time_zone))
        if category:
            query = query.where(ReadTxProjection.category_name == category)
        if account:
            query = query.where(
                or_(
                    ReadTxProjection.account_name == account,
                    ReadTxProjection.from_account_name == account,
                    ReadTxProjection.to_account_name == account,
                )
            )
        if min_amount is not None:
            query = query.where(func.abs(ReadTxProjection.amount) >= min_amount)
        if max_amount is not None:
            query = query.where(func.abs(ReadTxProjection.amount) <= max_amount)
        if q and q.strip():
            query = query.where(_search_condition(db, user.id, q.strip()))

        # 先取总数
        total_q = select(func.count()).select_from(query.subquery())
        total = int(db.scalar(total_q) or 0)

        if continuation:
            stamp = datetime.fromisoformat(continuation["time"])
            query = query.where(or_(ReadTxProjection.happened_at < stamp,
                                   and_(ReadTxProjection.happened_at == stamp,
                                        ReadTxProjection.sync_id < continuation["id"])))
        page_size = max(1, min(limit, 200))
        rows = db.scalars(query.order_by(ReadTxProjection.happened_at.desc(),
                                         ReadTxProjection.sync_id.desc()).limit(page_size + 1)).all()
        more = len(rows) > page_size
        rows = rows[:page_size]
        # Check again after fetching: never hand out an apparently valid page across a concurrent write.
        if revision(db, [led.id], led.user_id) != current_revision:
            raise ValueError("CURSOR_INVALIDATED: data changed during query; restart")

        return {
            "ledger": led.name,
            "ledger_id": led.external_id,
            "currency": led.currency,
            "revision": current_revision,
            "total": total,
            "has_more": more,
            "next_cursor": encode_cursor(dict(filter=signature, revision=current_revision,
                                               time=rows[-1].happened_at.isoformat(), id=rows[-1].sync_id)) if more else None,
            "items": [{**_serialize_tx(r, r.category_name), "ledger_id": led.external_id,
                       "currency_code": r.currency_code or led.currency} for r in rows],
        }


def get_transaction(user: User, sync_id: str, ledger_id: str | None = None) -> dict[str, Any] | None:
    """单条交易详情(按 sync_id,跨账本)。"""
    with SessionLocal() as db:
        ledgers = [led for led in live_ledgers(db, user.id) if not ledger_id or led.external_id == ledger_id]
        matches = db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.ledger_id.in_([led.id for led in ledgers]),
                ReadTxProjection.sync_id == sync_id,
            ).limit(2)
        ).all()
        if len(matches) > 1:
            raise ValueError('Ambiguous sync_id; specify ledger_id')
        if not matches:
            return None
        row = matches[0]
        led = db.scalar(select(Ledger).where(Ledger.id == row.ledger_id))
        out = _serialize_tx(row, row.category_name)
        out["ledger"] = led.name if led else None
        out["ledger_id"] = led.external_id if led else None
        out["currency_code"] = row.currency_code or (led.currency if led else None)
        out["attachments"] = json.loads(row.attachments_json or "[]") if row.attachments_json else []
        return out


def list_categories(user: User, *, kind: str | None = None, ledger_id: str | None = None) -> list[dict[str, Any]]:
    """列分类。kind 可选 'expense' / 'income' / 'transfer'。"""
    with SessionLocal() as db:
        query = select(UserCategoryProjection).where(UserCategoryProjection.user_id == _dictionary_owner(db, user, ledger_id))
        if kind:
            query = query.where(UserCategoryProjection.kind == kind)
        rows = db.scalars(query).all()
        # 跨账本去重 — 同 sync_id 取最新一份
        seen: dict[str, UserCategoryProjection] = {}
        for r in rows:
            if r.sync_id not in seen:
                seen[r.sync_id] = r
        return [
            {
                "id": r.sync_id,
                "name": r.name,
                "kind": r.kind,
                "level": r.level,
                "parent_name": r.parent_name,
                "parent_id": r.parent_sync_id,
                "icon": r.icon,
            }
            for r in sorted(
                seen.values(),
                key=lambda r: (r.kind or "", r.sort_order or 0, (r.name or "").lower()),
            )
        ]


def list_accounts(user: User, *, account_type: str | None = None, ledger_id: str | None = None) -> list[dict[str, Any]]:
    """列账户。account_type 可选 'bank_card' / 'credit_card' / 'cash' / 等。"""
    with SessionLocal() as db:
        query = select(UserAccountProjection).where(UserAccountProjection.user_id == _dictionary_owner(db, user, ledger_id))
        if account_type:
            query = query.where(UserAccountProjection.account_type == account_type)
        rows = db.scalars(query).all()
        seen: dict[str, UserAccountProjection] = {}
        for r in rows:
            if r.sync_id not in seen:
                seen[r.sync_id] = r
        return [
            {
                "id": r.sync_id,
                "name": r.name,
                "account_type": r.account_type,
                "currency": r.currency,
                "initial_balance": float(r.initial_balance or 0),
                "hidden": bool(r.hidden),
                "bank_name": r.bank_name,
                "card_last_four": r.card_last_four,
                "credit_limit": float(r.credit_limit) if r.credit_limit is not None else None,
                "billing_day": r.billing_day,
                "payment_due_day": r.payment_due_day,
            }
            for r in sorted(seen.values(), key=lambda r: (r.name or "").lower())
        ]


def list_tags(user: User, *, ledger_id: str | None = None) -> list[dict[str, Any]]:
    """列标签。"""
    with SessionLocal() as db:
        rows = db.scalars(
            select(UserTagProjection).where(UserTagProjection.user_id == _dictionary_owner(db, user, ledger_id))
        ).all()
        seen: dict[str, UserTagProjection] = {}
        for r in rows:
            if r.sync_id not in seen:
                seen[r.sync_id] = r
        return [{"id": r.sync_id, "name": r.name, "color": r.color} for r in sorted(seen.values(), key=lambda r: (r.name or "").lower())]


def list_budgets(user: User, *, ledger_id: str | None = None) -> list[dict[str, Any]]:
    """Use the production budget usage calculation, including parent categories and flags."""
    from ...routers.read.ledgers import list_budgets as budgets, list_budgets_usage
    with SessionLocal() as db:
        led = _resolve_ledger(db, user.id, ledger_id)
        if not led:
            return []
        values = budgets(ledger_external_id=led.external_id, _scopes=set(), current_user=user, db=db)
        usage = list_budgets_usage(ledger_external_id=led.external_id, _scopes=set(), current_user=user, db=db)
        used = {row.budget_id: row.used for row in usage.items}
        out = []
        for value in values:
            data = value.model_dump(mode="json")
            identifier = data.get("id") or data.get("sync_id")
            amount = float(data["amount"])
            spent = used.get(identifier, 0.0)
            out.append({**data, "id": identifier, "ledger_id": led.external_id, "currency": led.currency,
                        "spent": spent, "remaining": max(0, amount - spent),
                        "percent_used": round(spent / amount * 100, 1) if amount else 0,
                        "exceeded": spent > amount})
        return out


def get_ledger_stats(user: User, *, ledger_id: str | None = None) -> dict[str, Any] | None:
    """账本统计 — 交易数 / 分类数 / 账户数 / 标签数 / 预算数。"""
    with SessionLocal() as db:
        led = _resolve_ledger(db, user.id, ledger_id)
        if led is None:
            return None
        tx_count = int(db.scalar(
            select(func.count()).select_from(ReadTxProjection)
            .where(ReadTxProjection.ledger_id == led.id)
        ) or 0)
        category_count = int(db.scalar(
            select(func.count(func.distinct(UserCategoryProjection.sync_id)))
            .where(UserCategoryProjection.user_id == led.user_id)
        ) or 0)
        account_count = int(db.scalar(
            select(func.count(func.distinct(UserAccountProjection.sync_id)))
            .where(UserAccountProjection.user_id == led.user_id)
        ) or 0)
        tag_count = int(db.scalar(
            select(func.count(func.distinct(UserTagProjection.sync_id)))
            .where(UserTagProjection.user_id == led.user_id)
        ) or 0)
        budget_count = int(db.scalar(
            select(func.count()).select_from(ReadBudgetProjection)
            .where(ReadBudgetProjection.ledger_id == led.id)
        ) or 0)
        return {
            "ledger": led.name,
            "transaction_count": tx_count,
            "category_count": category_count,
            "account_count": account_count,
            "tag_count": tag_count,
            "budget_count": budget_count,
        }


def get_analytics_summary(
    user: User, *, scope: str = "month", period: str | None = None,
    ledger_id: str | None = None, tz_offset_minutes: int = 0,
) -> dict[str, Any]:
    """Share the production analytics calculation and return explicit drill-down context."""
    from ...routers.read.workspace import workspace_analytics
    if scope not in {"month", "year", "all"}:
        raise ValueError("scope must be month, year or all")
    if not -720 <= tz_offset_minutes <= 840:
        raise ValueError("Invalid timezone offset")
    with SessionLocal() as db:
        led = _resolve_ledger(db, user.id, ledger_id)
        if not led:
            return {}
        result = workspace_analytics(scope=scope, metric="expense", period=period,
                   ledger_id=led.external_id, user_id=led.user_id, tz_offset_minutes=tz_offset_minutes,
                   natural_month=False, _scopes=set(), current_user=user, db=db)
        summary = result.summary
        return dict(ledger=led.name, ledger_id=led.external_id, currency=led.currency,
                    scope=scope, period=result.range.period,
                    income=summary.income_total, expense=summary.expense_total, balance=summary.balance,
                    transaction_count=summary.transaction_count,
                    top_categories=[dict(name=row.category_name, total=row.total) for row in result.category_ranks[:10]],
                    series=[row.model_dump(mode="json") for row in result.series],
                    anomaly_months=[row.model_dump(mode="json") for row in result.anomaly_months],
                    range=result.range.model_dump(mode="json"),
                    calculation=dict(currency="ledger base; native_amount falls back to amount",
                                     exclude_from_stats=True, month_start_day=led.month_start_day,
                                     tz_offset_minutes=tz_offset_minutes,
                                     refund_policy="income and expense stay separate; no inferred refund pairing"))


def search(user: User, *, q: str, limit: int = 20) -> list[dict[str, Any]]:
    """全文模糊搜交易备注、分类、账户及标签（包括标签 ID 关联）。"""
    if not q.strip():
        return []
    with SessionLocal() as db:
        query = (
            select(ReadTxProjection)
            .where(
                ReadTxProjection.ledger_id.in_([led.id for led in live_ledgers(db, user.id)]),
                _search_condition(db, user.id, q.strip()),
            )
            .order_by(ReadTxProjection.happened_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        rows = db.scalars(query).all()
        return [_serialize_tx(r, r.category_name) for r in rows]


# ---------- internal helpers -------------------------------------------------


def _dictionary_owner(db, user, ledger_id):
    if ledger_id is None:
        return user.id
    ledger = _resolve_ledger(db, user.id, ledger_id)
    if ledger is None:
        raise ValueError('Ledger not found')
    return ledger.user_id


def _search_condition(db, user_id: str, text: str):
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    conditions = [column.ilike(pattern, escape="\\") for column in (
        ReadTxProjection.note,
        ReadTxProjection.category_name,
        ReadTxProjection.account_name,
        ReadTxProjection.from_account_name,
        ReadTxProjection.to_account_name,
        ReadTxProjection.tags_csv,
    )]
    ids = db.scalars(select(UserTagProjection.sync_id).where(
        UserTagProjection.user_id == user_id,
        UserTagProjection.name.ilike(pattern, escape="\\"),
    )).all()
    for sync_id in ids:
        conditions.append(ReadTxProjection.tag_sync_ids_json.like(
            f'%"{sync_id}"%', escape="\\"
        ))
    return or_(*conditions)


def _parse_dt(value: str, *, end_of_day: bool = False) -> datetime:
    """ISO date / datetime → datetime(UTC)。LLM 经常给 '2026-05-13'。"""
    value = value.strip()
    if len(value) == 10:
        # YYYY-MM-DD
        dt = datetime.strptime(value, "%Y-%m-%d")
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt.replace(tzinfo=timezone.utc)
    # 尝试完整 ISO
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
