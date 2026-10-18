"""贷款本金契约。HTTP 写入声明能力，离线投影重建不改变原始历史。"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import math

from fastapi import HTTPException
from sqlalchemy import func, select

from .models import Ledger, LedgerMember, ReadTxProjection, User, UserAccountProjection

loan_capable: ContextVar[bool | None] = ContextVar("loan_capable", default=None)


def fail(code: str, status: int = 400):
    raise HTTPException(status_code=status, detail={"error_code": code, "message": code})


def utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def account_movement(db, user_id: str, account_id: str, *, since=None) -> float:
    # 与 App 的私人账户余额一致，共享账本不会移动本方私人余额。
    shared = select(LedgerMember.ledger_id).group_by(LedgerMember.ledger_id).having(func.count() > 1)
    rows = db.scalars(select(ReadTxProjection).join(Ledger, Ledger.id == ReadTxProjection.ledger_id)
                      .where(Ledger.user_id == user_id, Ledger.id.not_in(shared))).all()
    return sum(movement(row_state(row), account_id) for row in rows
               if since is None or (row.happened_at is not None and utc(row.happened_at) >= utc(since)))


def row_state(row):
    if row is None:
        return None
    return dict(type=row.tx_type, amount=row.amount,
                accountId=row.from_account_sync_id or row.account_sync_id,
                toAccountId=row.to_account_sync_id, happenedAt=row.happened_at)


def movement(item, account_id):
    if not item:
        return 0.0
    amount = float(item.get("amount") or 0)
    source = item.get("fromAccountId") or item.get("accountId")
    if source == account_id:
        return amount if item.get("type") == "income" else -amount
    return amount if item.get("type") == "transfer" and item.get("toAccountId") == account_id else 0.0


def check_account(db, user_id, payload, *, sync_id=None):
    if loan_capable.get() is None:
        return
    old = db.get(UserAccountProjection, (user_id, sync_id or payload.get("syncId")))
    is_loan = payload.get("type", old.account_type if old else None) == "loan"
    if not is_loan and not (old and old.account_type == "loan"):
        return
    version = payload.get("loanBalanceVersion", old.loan_balance_version if old else 0)
    if (version == 1 or (old and old.loan_balance_version == 1)) and not loan_capable.get():
        fail("LOAN_CLIENT_UPGRADE_REQUIRED", 426)
    if old and old.account_type == "loan" and not is_loan:
        fail("LOAN_ACCOUNT_DOWNGRADE_FORBIDDEN", 409)
    if old and old.loan_balance_version == 1 and version != 1:
        fail("LOAN_ACCOUNT_DOWNGRADE_FORBIDDEN", 409)
    if old and old.account_type != 'loan' and is_loan:
        fail("LOAN_ACCOUNT_DOWNGRADE_FORBIDDEN", 409)
    if old and payload.get('currency', old.currency) != old.currency and db.scalar(
            select(func.count()).select_from(ReadTxProjection).where(
                ReadTxProjection.user_id == user_id,
                (ReadTxProjection.account_sync_id == old.sync_id) |
                (ReadTxProjection.from_account_sync_id == old.sync_id) |
                (ReadTxProjection.to_account_sync_id == old.sync_id))):
        fail("LOAN_SAME_CURRENCY_ONLY")
    if version == 1:
        initial = float(payload.get("initialBalance", old.initial_balance if old else 0) or 0)
        if not math.isfinite(initial):
            fail("LOAN_INVALID_AMOUNT")
        sid = sync_id or payload.get("syncId")
        since = utc(payload.get("loanBalanceSince", old.loan_balance_since if old else None))
        if since is None or since > datetime.now(timezone.utc):
            fail("LOAN_BASELINE_REQUIRED")
        if initial + account_movement(db, user_id, sid, since=since) > 0.0000001:
            fail("LOAN_REPAYMENT_EXCEEDS_DEBT")



def check_transaction(db, *, ledger_id, payload=None, sync_id=None, allow_history=False):
    if loan_capable.get() is None:
        return
    ledger = db.get(Ledger, ledger_id)
    if ledger is None:
        return
    old_row = db.get(ReadTxProjection, (ledger_id, sync_id or (payload or {}).get("syncId")))
    old = row_state(old_row)
    new = dict(payload) if payload is not None else None
    if new is not None:
        # 与投影的历史名称回退一致，先解析后校验，不能用省略 ID 绕过贷款规则。
        from .projection import _resolve_account_sync_id_by_name
        for key, name in (("accountId", "accountName"), ("fromAccountId", "fromAccountName"), ("toAccountId", "toAccountName")):
            if not new.get(key) and new.get(name):
                new[key] = _resolve_account_sync_id_by_name(db, user_id=ledger.user_id, name=new[name])
    refs = set()
    for item in (old, new):
        if item:
            refs.update(x for x in (item.get("accountId"), item.get("fromAccountId"), item.get("toAccountId")) if x)
    if not refs:
        return
    accounts = db.scalars(select(UserAccountProjection).where(
        UserAccountProjection.user_id == ledger.user_id, UserAccountProjection.sync_id.in_(refs))).all()
    loans = [a for a in accounts if a.account_type == "loan"]
    if not loans:
        return
    if not loan_capable.get():
        fail("LOAN_CLIENT_UPGRADE_REQUIRED", 426)
    # 跨账本写同一贷款时序列化该用户的本金校验；SQLite 写事务本身串行。
    db.execute(select(User.id).where(User.id == ledger.user_id).with_for_update()).all()
    if db.scalar(select(func.count()).select_from(LedgerMember).where(LedgerMember.ledger_id == ledger_id)) > 1:
        fail("LOAN_PERSONAL_LEDGER_ONLY")
    for loan in loans:
        sid = loan.sync_id
        financial_keys = ("type", "amount", "accountId", "toAccountId", "happenedAt")
        normalized_new = None if new is None else {**new, "accountId": new.get("fromAccountId") or new.get("accountId")}
        changed = old is None or normalized_new is None or any(
            (utc(old.get(k)) != utc(normalized_new.get(k))) if k == "happenedAt" else
            old.get(k) != normalized_new.get(k) for k in financial_keys)
        if not changed:
            continue
        # 原生同步可补齐接续前的已有记录。这些记录不移动接续后的贷款本金，
        # 也不能借此修改/删除既有历史；Web 写接口没有此入口。
        historical_insert = (allow_history and old is None and new is not None and
            new.get('loanHistoryImport') is True and (loan.loan_balance_version == 0 or
            (loan.loan_balance_since and utc(new.get('happenedAt')) < utc(loan.loan_balance_since))))
        if historical_insert:
            amount = float(new.get("amount") or 0)
            if not math.isfinite(amount) or amount <= 0:
                fail("LOAN_INVALID_AMOUNT")
            continue
        if loan.loan_balance_version != 1:
            fail("LOAN_NEEDS_REVIEW", 409)
        if new and sid in (normalized_new.get("accountId"), new.get("toAccountId")):
            amount = float(new.get("amount") or 0)
            if new.get("type") != "transfer" or not math.isfinite(amount) or amount <= 0:
                fail("LOAN_TRANSFER_ONLY")
            pair = {normalized_new.get("accountId"), new.get("toAccountId")}
            if None in pair or len(pair) != 2 or len([a for a in accounts if a.sync_id in pair]) != 2:
                fail("LOAN_TRANSFER_ONLY")
            if any(a.account_type in ('real_estate', 'vehicle', 'investment', 'insurance', 'social_fund') for a in accounts if a.sync_id in pair):
                fail("LOAN_TRANSFER_ONLY")
            if len({a.currency for a in accounts if a.sync_id in pair}) != 1:
                fail("LOAN_SAME_CURRENCY_ONLY")
        if changed and loan.loan_balance_since:
            since = utc(loan.loan_balance_since)
            if any(item and utc(item.get("happenedAt")) and utc(item.get("happenedAt")) < since for item in (old, new)):
                fail("LOAN_BEFORE_BASELINE", 409)
        balance = float(loan.initial_balance or 0) + account_movement(db, ledger.user_id, sid, since=loan.loan_balance_since)
        if balance - movement(old, sid) + movement(new, sid) > 0.0000001:
            fail("LOAN_REPAYMENT_EXCEEDS_DEBT")


def upsert_account(db, *, user_id, source_change_id, payload):
    from . import projection
    check_account(db, user_id, payload)
    return projection.upsert_account(db, user_id=user_id, source_change_id=source_change_id, payload=payload)


def upsert_tx(db, *, ledger_id, user_id, source_change_id, payload, allow_history=False):
    from . import projection
    check_transaction(db, ledger_id=ledger_id, payload=payload, allow_history=allow_history)
    return projection.upsert_tx(db, ledger_id=ledger_id, user_id=user_id, source_change_id=source_change_id, payload=payload)


def delete_tx(db, *, ledger_id, sync_id):
    from . import projection
    check_transaction(db, ledger_id=ledger_id, sync_id=sync_id)
    return projection.delete_tx(db, ledger_id=ledger_id, sync_id=sync_id)


def delete_account(db, *, user_id, sync_id):
    from . import projection
    old = db.get(UserAccountProjection, (user_id, sync_id))
    if old and old.account_type == "loan":
        check_account(db, user_id, {}, sync_id=sync_id)
    return projection.delete_account(db, user_id=user_id, sync_id=sync_id)


def upsert_synced_tx(db, **kwargs):
    return upsert_tx(db, **kwargs, allow_history=True)
