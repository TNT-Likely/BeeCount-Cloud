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

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ...database import SessionLocal
from ...models import (
    Ledger,
    ReadAccountProjection,
    ReadBudgetProjection,
    ReadCategoryProjection,
    ReadTagProjection,
    ReadTxProjection,
    User,
)


# ---------- helpers ----------------------------------------------------------


def _resolve_ledger(
    db: Session, user_id: str, ledger_id: str | None
) -> Ledger | None:
    """LLM 友好的 ledger 解析:
      - ledger_id 是 external_id(用户视角的 id,如 "1" / UUID)→ 按 external_id 查
      - 空 → 拿用户的第一个账本(active fallback)
      - 不存在 → None
    """
    if ledger_id:
        led = db.scalar(
            select(Ledger).where(
                Ledger.user_id == user_id,
                Ledger.external_id == ledger_id,
            )
        )
        return led
    # fallback 第一个账本(按 created_at)
    return db.scalar(
        select(Ledger)
        .where(Ledger.user_id == user_id)
        .order_by(Ledger.created_at.asc())
        .limit(1)
    )


def _serialize_tx(row: ReadTxProjection, category_name: str | None) -> dict[str, Any]:
    return {
        "sync_id": row.sync_id,
        "tx_type": row.tx_type,
        "amount": float(row.amount or 0),
        "happened_at": row.happened_at.isoformat() if row.happened_at else None,
        "note": row.note,
        "category_name": category_name or row.category_name,
        "account_name": row.account_name,
        "from_account_name": row.from_account_name,
        "to_account_name": row.to_account_name,
        "tags": row.tags_csv or "",
    }


# ---------- tool implementations --------------------------------------------


def list_ledgers(user: User) -> list[dict[str, Any]]:
    """列出当前用户的所有账本。返回 external_id / name / currency / created_at。"""
    with SessionLocal() as db:
        rows = db.scalars(
            select(Ledger)
            .where(Ledger.user_id == user.id)
            .order_by(Ledger.created_at.asc())
        ).all()
        return [
            {
                "id": r.external_id,
                "name": r.name,
                "currency": r.currency,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
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

        query = select(ReadTxProjection).where(ReadTxProjection.ledger_id == led.id)
        if date_from:
            query = query.where(ReadTxProjection.happened_at >= _parse_dt(date_from))
        if date_to:
            query = query.where(ReadTxProjection.happened_at <= _parse_dt(date_to, end_of_day=True))
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
        if q:
            query = query.where(ReadTxProjection.note.ilike(f"%{q}%"))

        # 先取总数
        total_q = select(func.count()).select_from(query.subquery())
        total = int(db.scalar(total_q) or 0)

        rows = db.scalars(
            query.order_by(ReadTxProjection.happened_at.desc()).limit(max(1, min(limit, 200)))
        ).all()

        return {
            "ledger": led.name,
            "total": total,
            "items": [_serialize_tx(r, r.category_name) for r in rows],
        }


def get_transaction(user: User, sync_id: str) -> dict[str, Any] | None:
    """单条交易详情(按 sync_id,跨账本)。"""
    with SessionLocal() as db:
        row = db.scalar(
            select(ReadTxProjection).where(
                ReadTxProjection.user_id == user.id,
                ReadTxProjection.sync_id == sync_id,
            )
        )
        if row is None:
            return None
        led = db.scalar(select(Ledger).where(Ledger.id == row.ledger_id))
        out = _serialize_tx(row, row.category_name)
        out["ledger"] = led.name if led else None
        out["attachments"] = json.loads(row.attachments_json or "[]") if row.attachments_json else []
        return out


def list_categories(user: User, *, kind: str | None = None) -> list[dict[str, Any]]:
    """列分类。kind 可选 'expense' / 'income' / 'transfer'。"""
    with SessionLocal() as db:
        query = select(ReadCategoryProjection).where(ReadCategoryProjection.user_id == user.id)
        if kind:
            query = query.where(ReadCategoryProjection.kind == kind)
        rows = db.scalars(query).all()
        # 跨账本去重 — 同 sync_id 取最新一份
        seen: dict[str, ReadCategoryProjection] = {}
        for r in rows:
            if r.sync_id not in seen:
                seen[r.sync_id] = r
        return [
            {
                "name": r.name,
                "kind": r.kind,
                "level": r.level,
                "parent_name": r.parent_name,
                "icon": r.icon,
            }
            for r in sorted(
                seen.values(),
                key=lambda r: (r.kind or "", r.sort_order or 0, (r.name or "").lower()),
            )
        ]


def list_accounts(user: User, *, account_type: str | None = None) -> list[dict[str, Any]]:
    """列账户。account_type 可选 'bank_card' / 'credit_card' / 'cash' / 等。"""
    with SessionLocal() as db:
        query = select(ReadAccountProjection).where(ReadAccountProjection.user_id == user.id)
        if account_type:
            query = query.where(ReadAccountProjection.account_type == account_type)
        rows = db.scalars(query).all()
        seen: dict[str, ReadAccountProjection] = {}
        for r in rows:
            if r.sync_id not in seen:
                seen[r.sync_id] = r
        return [
            {
                "name": r.name,
                "account_type": r.account_type,
                "currency": r.currency,
                "initial_balance": float(r.initial_balance or 0),
                "bank_name": r.bank_name,
                "card_last_four": r.card_last_four,
                "credit_limit": float(r.credit_limit) if r.credit_limit is not None else None,
                "billing_day": r.billing_day,
                "payment_due_day": r.payment_due_day,
            }
            for r in sorted(seen.values(), key=lambda r: (r.name or "").lower())
        ]


def list_tags(user: User) -> list[dict[str, Any]]:
    """列标签。"""
    with SessionLocal() as db:
        rows = db.scalars(
            select(ReadTagProjection).where(ReadTagProjection.user_id == user.id)
        ).all()
        seen: dict[str, ReadTagProjection] = {}
        for r in rows:
            if r.sync_id not in seen:
                seen[r.sync_id] = r
        return [{"name": r.name, "color": r.color} for r in sorted(seen.values(), key=lambda r: (r.name or "").lower())]


def list_budgets(user: User, *, ledger_id: str | None = None) -> list[dict[str, Any]]:
    """列预算 + 当月已用进度。"""
    with SessionLocal() as db:
        led = _resolve_ledger(db, user.id, ledger_id)
        if led is None:
            return []
        rows = db.scalars(
            select(ReadBudgetProjection).where(ReadBudgetProjection.ledger_id == led.id)
        ).all()
        # 取当月支出聚合(用于计算进度)
        from datetime import datetime as _dt
        now = _dt.now()
        month_start = _dt(now.year, now.month, 1, tzinfo=timezone.utc)
        spent_by_cat: dict[str | None, float] = {}
        total_expense = 0.0
        tx_rows = db.execute(
            select(ReadTxProjection.category_sync_id, func.sum(ReadTxProjection.amount))
            .where(
                ReadTxProjection.ledger_id == led.id,
                ReadTxProjection.tx_type == "expense",
                ReadTxProjection.happened_at >= month_start,
            )
            .group_by(ReadTxProjection.category_sync_id)
        ).all()
        for cat_id, amt in tx_rows:
            v = float(amt or 0)
            spent_by_cat[cat_id] = v
            total_expense += v

        out: list[dict[str, Any]] = []
        for b in rows:
            spent = spent_by_cat.get(b.category_sync_id) if b.budget_type == "category" else total_expense
            amount = float(b.amount or 0)
            pct = (spent or 0) / amount * 100 if amount else 0
            out.append({
                "id": b.sync_id,
                "type": b.budget_type or "total",
                "amount": amount,
                "spent": spent or 0,
                "remaining": max(0, amount - (spent or 0)),
                "percent_used": round(pct, 1),
                "exceeded": pct > 100,
            })
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
            select(func.count(func.distinct(ReadCategoryProjection.sync_id)))
            .where(ReadCategoryProjection.user_id == user.id)
        ) or 0)
        account_count = int(db.scalar(
            select(func.count(func.distinct(ReadAccountProjection.sync_id)))
            .where(ReadAccountProjection.user_id == user.id)
        ) or 0)
        tag_count = int(db.scalar(
            select(func.count(func.distinct(ReadTagProjection.sync_id)))
            .where(ReadTagProjection.user_id == user.id)
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
    user: User,
    *,
    scope: str = "month",
    period: str | None = None,
    ledger_id: str | None = None,
) -> dict[str, Any]:
    """分析数据。scope:'month' | 'year' | 'all'。

    返回:总收入 / 总支出 / 净额 + 分类排名 top 10。
    """
    with SessionLocal() as db:
        led = _resolve_ledger(db, user.id, ledger_id)
        if led is None:
            return {}

        query = select(ReadTxProjection).where(ReadTxProjection.ledger_id == led.id)
        now = datetime.now(timezone.utc)
        if scope == "month":
            year, month = now.year, now.month
            if period:
                try:
                    year, month = [int(p) for p in period.split("-")[:2]]
                except Exception:
                    pass
            start = datetime(year, month, 1, tzinfo=timezone.utc)
            if month == 12:
                end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
            query = query.where(ReadTxProjection.happened_at >= start, ReadTxProjection.happened_at < end)
        elif scope == "year":
            year = now.year
            if period:
                try:
                    year = int(period[:4])
                except Exception:
                    pass
            start = datetime(year, 1, 1, tzinfo=timezone.utc)
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            query = query.where(ReadTxProjection.happened_at >= start, ReadTxProjection.happened_at < end)
        # scope == "all" 不加时间过滤

        rows = db.scalars(query).all()
        income = sum(float(r.amount or 0) for r in rows if r.tx_type == "income")
        expense = sum(float(r.amount or 0) for r in rows if r.tx_type == "expense")

        # 分类排名 — 按支出金额 top 10
        cat_total: dict[str, float] = {}
        for r in rows:
            if r.tx_type != "expense":
                continue
            name = r.category_name or "(未分类)"
            cat_total[name] = cat_total.get(name, 0) + float(r.amount or 0)
        ranks = sorted(cat_total.items(), key=lambda x: -x[1])[:10]

        return {
            "ledger": led.name,
            "scope": scope,
            "period": period,
            "income": round(income, 2),
            "expense": round(expense, 2),
            "balance": round(income - expense, 2),
            "transaction_count": len(rows),
            "top_categories": [{"name": n, "total": round(v, 2)} for n, v in ranks],
        }


def search(user: User, *, q: str, limit: int = 20) -> list[dict[str, Any]]:
    """全文模糊搜交易备注 / 分类名 / 账户名。"""
    if not q.strip():
        return []
    with SessionLocal() as db:
        query = (
            select(ReadTxProjection)
            .where(
                ReadTxProjection.user_id == user.id,
                or_(
                    ReadTxProjection.note.ilike(f"%{q}%"),
                    ReadTxProjection.category_name.ilike(f"%{q}%"),
                    ReadTxProjection.account_name.ilike(f"%{q}%"),
                ),
            )
            .order_by(ReadTxProjection.happened_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        rows = db.scalars(query).all()
        return [_serialize_tx(r, r.category_name) for r in rows]


# ---------- internal helpers -------------------------------------------------


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
