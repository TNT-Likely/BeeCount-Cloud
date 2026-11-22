"""Web-first REST endpoints for Contacts & Loans.

These endpoints manage the server-direct `contacts` / `loans` / `loan_repayments`
tables. They are intentionally OUTSIDE the mobile sync layer:
  - Contacts are user-global.
  - Loans & repayments are ledger-scoped.
  - 应收款 / 应付款 are *virtual* accounts derived live from outstanding loan
    balances — they never create real transactions or touch real account
    balances.

Status derivation (priority): settled > overdue > partial > unpaid.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..linked_tx import linked_tx_map as _linked_tx_map
from ..deps import get_current_user
from ..models import (
    AttachmentFile,
    Contact,
    Ledger,
    Loan,
    LoanPaymentGroup,
    LoanRepayment,
    ReadTxProjection,
    SyncChange,
    User,
    UserAccountProjection,
)
from ..projection import delete_tx, upsert_tx
from ..schemas_linked import LinkedTxOut
from ..schemas_cl import (
    ContactCreate,
    ContactLoanRankOut,
    ContactOut,
    ContactSummaryOut,
    ContactUpdate,
    LoanCreate,
    LoanDirection,
    LoanOut,
    LoanUpdate,
    MergedRepaymentCreate,
    MergedRepaymentOut,
    ReminderItem,
    RemindersOut,
    RepaymentCreate,
    RepaymentOut,
    SuggestContactOut,
    VirtualAccountsOut,
)

contacts_router = APIRouter()
loans_router = APIRouter()

# 临近到期窗口:约定还款日落在 [now, now + 3 天] 内(且未结清)算"临期提醒"。
NEAR_DUE_DAYS = 3

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _json_loads_safe(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except (ValueError, TypeError):
        return []
    return []


def _json_dumps(value: Optional[list[str]]) -> Optional[str]:
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False)


def _attachment_refs_from_ids(
    db: Session, *, user_id: str, file_ids: Optional[list[str]]
) -> list[dict]:
    """把借款凭证 / 还款凭证的 file id 转成交易流水用的 AttachmentRef 形态。

    写进 read_tx_projection.attachments_json 后,交易详情就能直接预览这些图片;
    文件名从 attachment_files 反查,查不到(文件已删)时退回 file id。
    """
    refs: list[dict] = []
    for file_id in file_ids or []:
        fid = (file_id or "").strip()
        if not fid:
            continue
        row = db.get(AttachmentFile, fid)
        name = row.file_name if row is not None and row.user_id == user_id else fid
        refs.append(
            {
                "fileName": f"{fid}_{name}",
                "originalName": name,
                "cloudFileId": fid,
                "fileSize": row.size_bytes if row is not None else None,
                "sortOrder": len(refs),
            }
        )
    return refs


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes for tz-aware columns; normalize to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _compute_status(
    amount: float, total_repaid: float, due_at: Optional[datetime], now: datetime
) -> str:
    now_utc = _utc(now)
    due_utc = _utc(due_at)
    if total_repaid >= amount - 0.005:
        return "settled"
    if due_utc is not None and due_utc <= now_utc:
        return "overdue"
    if total_repaid > 0:
        return "partial"
    return "unpaid"


def _sum_repaid(db: Session, loan_id: str) -> float:
    row = db.execute(
        select(func.coalesce(func.sum(LoanRepayment.amount), 0.0)).where(
            LoanRepayment.loan_id == loan_id
        )
    ).scalar_one()
    return float(row)


def _person_loan_pool(
    db: Session, *, contact_id: Optional[str], direction: str, ledger_id: str, user_id: str
) -> list[Loan]:
    """同联系人 + 同方向的全部借款(按发生时间升序),用于合并还款的 FIFO 分配。

    「同一人同方向」是合并还款的结算单元:一次实际收付款可以同时结清这个单元下
    的多笔借款(例:张三 2025-01-01 借出 50+50+50,收到一笔 170)。
    """
    stmt = select(Loan).where(
        Loan.ledger_id == ledger_id,
        Loan.user_id == user_id,
        Loan.direction == direction,
    )
    if contact_id:
        stmt = stmt.where(Loan.contact_id == contact_id)
    else:
        # 无联系人的散账不合并(避免把互不相关的记录归到同一笔收付款里)。
        return []
    stmt = stmt.order_by(Loan.happened_at.asc(), Loan.id.asc())
    return list(db.scalars(stmt))


def _allocate_repayment_to_loans(
    db: Session, *, loans: list[Loan], principal: float
) -> list[tuple[Loan, float]]:
    """把一笔本金按 FIFO 分配到多笔借款上,返回 [(借款, 该笔分摊金额)]。

    先借先还:按发生时间升序依次冲抵,每笔最多冲抵它自己的剩余本金。
    """
    remaining = round(float(principal), 2)
    allocations: list[tuple[Loan, float]] = []
    for loan in loans:
        if remaining <= 0.005:
            break
        already = _sum_repaid(db, loan.id)
        outstanding = round(float(loan.amount) - already, 2)
        if outstanding <= 0.005:
            continue
        chunk = round(min(outstanding, remaining), 2)
        allocations.append((loan, chunk))
        remaining = round(remaining - chunk, 2)
    return allocations


def sync_loan_from_flow_edit(
    db: Session,
    *,
    ledger_id: str,
    tx_sync_id: str,
    new_amount: Optional[float],
    discount_amount: float = 0.0,
) -> bool:
    """交易流水侧编辑金额/优惠 → 反向同步对应借款/还款(0027 / 0042)。

    由 write 层 update_tx 在提交成功后调用:借款引擎生成的流水(tx_sync_id)
    被直接改金额时,把变更写回 Loan.amount / LoanRepayment.amount 并重算
    借款状态(settled/overdue/partial/unpaid)。不回写流水(流水刚被用户改过,
    回写会覆盖用户输入),不重生成流水。

    0042 补充:
      - 合并还款(0039):流水金额 = 合并本金 + 利息,编辑时按比例调整组内各笔
        分摊行的本金,保证「各笔已还本金之和 = 流水金额 - 利息」;
      - 含利息的单笔还款:流水金额 = 本金 + 利息,编辑时只调整本金部分
        (本金 = 新金额 - 利息),利息不变;
      - 带支出优惠(0023)的借出流水:本金 = 流水金额 + 优惠额(= 实际到账)。
        实付 299.85 / 优惠 0.15 → 本金 300,不再记成 299.85。

    返回 True 表示命中了借款实体并已提交;False 表示该流水与借款无关。
    """
    if new_amount is None or new_amount <= 0:
        return False
    new_amount = float(new_amount)
    now = datetime.now(timezone.utc)

    # 1) 合并还款组的主流水:按比例调整组内分摊行。
    group = db.scalar(
        select(LoanPaymentGroup).where(
            LoanPaymentGroup.tx_sync_id == tx_sync_id,
            LoanPaymentGroup.ledger_id == ledger_id,
        )
    )
    if group is not None:
        rows = list(
            db.scalars(
                select(LoanRepayment).where(LoanRepayment.group_id == group.id)
            ).all()
        )
        interest = float(group.interest_amount or 0)
        principal_total = round(new_amount - interest, 2)
        if principal_total < 0:
            principal_total = 0.0
        old_total = round(sum(float(r.amount or 0) for r in rows), 2)
        if rows and old_total > 0:
            # 按各笔原分摊比例分配新本金;最后一行兜底保证总额精确。
            allocated = 0.0
            for row in rows[:-1]:
                share = round(float(row.amount or 0) / old_total * principal_total, 2)
                row.amount = share
                allocated = round(allocated + share, 2)
            rows[-1].amount = round(principal_total - allocated, 2)
        elif rows:
            rows[0].amount = principal_total
        group.amount = principal_total
        affected = {r.loan_id for r in rows}
        for lid in affected:
            parent = db.get(Loan, lid)
            if parent is not None:
                _recompute_loan(db, parent)
        db.commit()
        return True

    # 2) 借款本金流水。
    loan = db.scalar(
        select(Loan).where(Loan.tx_sync_id == tx_sync_id, Loan.ledger_id == ledger_id)
    )
    if loan is not None:
        # 借款本金 = **实际到账金额**。带支出优惠(0023)时流水 amount 是实付,
        # 实际到账 = 实付 + 优惠(成孝林 2025-02-17:实付 299.85 + 优惠 0.15 = 300)。
        # 忽略优惠会把本金记成 299.85,导致「还款 300 却比本金多」的账实不符。
        loan.amount = round(new_amount + float(discount_amount or 0), 2)
        loan.status = _compute_status(  # type: ignore[assignment]
            loan.amount, _sum_repaid(db, loan.id), loan.due_at, now
        )
        loan.updated_at = now
        db.commit()
        return True

    # 3) 单笔还款流水:金额含利息时只调整本金部分。
    repayment = db.scalar(
        select(LoanRepayment).where(
            LoanRepayment.tx_sync_id == tx_sync_id, LoanRepayment.ledger_id == ledger_id
        )
    )
    if repayment is not None:
        interest = float(getattr(repayment, "interest_amount", None) or 0)
        principal = round(new_amount - interest, 2)
        repayment.amount = max(0.0, principal)
        parent = db.get(Loan, repayment.loan_id)
        if parent is not None:
            parent.status = _compute_status(  # type: ignore[assignment]
                parent.amount, _sum_repaid(db, parent.id), parent.due_at, now
            )
            parent.updated_at = now
        db.commit()
        return True

    return False


def _loan_out(
    loan: Loan,
    contact_name: Optional[str],
    total_repaid: float,
    tx_details: Optional[list[LinkedTxOut]] = None,
) -> LoanOut:
    now = datetime.now(timezone.utc)
    return LoanOut(
        id=loan.id,
        user_id=loan.user_id,
        ledger_id=loan.ledger_id,
        contact_id=loan.contact_id,
        contact_name=contact_name,
        direction=loan.direction,  # type: ignore[arg-type]
        amount=loan.amount,
        currency_code=loan.currency_code,
        happened_at=loan.happened_at,
        due_at=loan.due_at,
        interest_type=loan.interest_type,  # type: ignore[arg-type]
        interest_rate=loan.interest_rate,
        interest_amount=loan.interest_amount,
        note=loan.note,
        voucher_file_ids=_json_loads_safe(loan.voucher_file_ids_json),
        status=_compute_status(loan.amount, total_repaid, loan.due_at, now),  # type: ignore[arg-type]
        total_repaid=total_repaid,
        account_id=loan.account_id,
        account_name=loan.account_name,
        tx_sync_id=loan.tx_sync_id,
        tx_sync_details=tx_details or [],
        created_at=loan.created_at,
        updated_at=loan.updated_at,
    )


def _repayment_out(
    repayment: LoanRepayment,
    group: Optional[LoanPaymentGroup] = None,
    group_size: int = 0,
    tx_details: Optional[list[LinkedTxOut]] = None,
    interest_tx_details: Optional[list[LinkedTxOut]] = None,
) -> RepaymentOut:
    """还款输出;附件 id 存 JSON 文本,统一转成空数组友好的 list。

    合并还款(0039):group 为该行所属的「一次实际收付款」组时,附带组信息
    (合并后的本金合计 / 被合并的借款笔数 / 被合并的原单流水列表)。
    """
    return RepaymentOut(
        id=repayment.id,
        user_id=repayment.user_id,
        ledger_id=repayment.ledger_id,
        loan_id=repayment.loan_id,
        amount=repayment.amount,
        repaid_at=repayment.repaid_at,
        note=repayment.note,
        account_id=repayment.account_id,
        account_name=repayment.account_name,
        tx_sync_id=repayment.tx_sync_id,
        attachment_file_ids=_json_loads_safe(repayment.attachment_file_ids_json),
        interest_amount=getattr(repayment, "interest_amount", None),
        interest_account_id=getattr(repayment, "interest_account_id", None),
        interest_account_name=getattr(repayment, "interest_account_name", None),
        interest_tx_sync_id=getattr(repayment, "interest_tx_sync_id", None),
        group_id=getattr(repayment, "group_id", None),
        # 0041:利息并入流水金额,group_amount 返回**实际流水金额**(本金+利息),
        # 与交易列表里显示的那一笔保持一致。
        group_amount=(
            round(float(group.amount) + float(group.interest_amount or 0), 2)
            if group is not None
            else None
        ),
        tx_sync_details=tx_details or [],
        interest_tx_sync_details=interest_tx_details or [],
        group_size=group_size,
        group_merged_from_sync_ids=(
            _json_loads_safe(group.merged_from_sync_ids_json) if group is not None else []
        ),
        created_at=repayment.created_at,
    )


def _recompute_loan(db: Session, loan: Loan) -> None:
    total = _sum_repaid(db, loan.id)
    now = datetime.now(timezone.utc)
    loan.status = _compute_status(loan.amount, total, loan.due_at, now)


def _get_contact_or_404(db: Session, user: User, contact_id: str) -> Contact:
    contact = db.get(Contact, contact_id)
    if contact is None or contact.user_id != user.id:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


def _require_contact_available(db: Session, user: User, contact_id: str) -> Contact:
    """新增/改绑借款前的可用性校验:归档与停用联系人禁止产生新往来。

    历史借款、还款与统计不受影响——只拦"新引用"。
    """
    contact = _get_contact_or_404(db, user, contact_id)
    if contact.is_archived or not contact.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="联系人已归档或停用，无法新增借款",
        )
    return contact


def _get_loan_or_404(db: Session, user: User, loan_id: str) -> Loan:
    loan = db.get(Loan, loan_id)
    if loan is None or loan.user_id != user.id:
        raise HTTPException(status_code=404, detail="Loan not found")
    return loan


# --------------------------------------------------------------------------
# Loan flow (real transaction generation)
# --------------------------------------------------------------------------
# 借款/还款会生成一笔**真实交易流水**(expense/income),挂到用户选中的真实账户上,
# 并 excludeFromStats/excludeFromBudget=True —— 既影响账户余额、出现在交易列表,
# 又不污染月度收入/支出汇总(借款不是真实收支)。流水与借款通过 tx_sync_id 互相关联,
# 借款编辑/删除时联动维护这笔流水(保持账实一致)。

_LOAN_FLOW_DEVICE_ID = "loan-engine"


def _require_account_or_404(db: Session, user: User, account_id: Optional[str]) -> UserAccountProjection:
    if not account_id:
        raise HTTPException(status_code=400, detail="loan requires an account")
    account = db.scalar(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == user.id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def _fmt_amount(value: float) -> str:
    """金额格式化:整数不显示小数位,小数保留两位(用于备注文案)。"""
    v = round(float(value), 2)
    if abs(v - round(v)) < 0.005:
        return str(int(round(v)))
    return f"{v:.2f}"


def _build_loan_flow_payload(
    *,
    direction: str,
    is_repayment: bool,
    amount: float,
    currency_code: Optional[str],
    happened_at: datetime,
    account_id: str,
    account_name: Optional[str],
    contact_name: Optional[str],
    note: Optional[str],
    attachments: Optional[list[dict]] = None,
    interest: Optional[float] = None,
) -> dict:
    """构造一笔借款/还款流水的交易 payload(喂给 snapshot_mutator.create_transaction)。

    attachments 恒带这个 key(空数组也算):snapshot_mutator 的 update 路径靠
    `attachments in payload` 判断是否清空旧附件,缺 key 会留下已删的凭证。

    interest(0041):利息**不单独成流水**(与实际银行流水不符 —— 实收一笔就是一笔),
    而是并入本笔流水的金额,并在备注里注明「含利息 X 元」。
    """
    if is_repayment:
        tx_type = "income" if direction == "lend" else "expense"
        label = "收回借款" if direction == "lend" else "偿还借款"
    else:
        tx_type = "expense" if direction == "lend" else "income"
        label = "借出" if direction == "lend" else "借入"
    parts = [label]
    if contact_name:
        parts.append(contact_name)
    if note:
        parts.append(note)
    if interest is not None and float(interest) > 0:
        parts.append(f"含利息 {_fmt_amount(float(interest))} 元")
    return {
        "tx_type": tx_type,
        "amount": amount,
        "currency_code": currency_code,
        "happened_at": happened_at.isoformat() if isinstance(happened_at, datetime) else happened_at,
        "account_id": account_id,
        "account_name": account_name,
        "category_name": label,
        "category_kind": "expense" if tx_type == "expense" else "income",
        "note": " · ".join(parts),
        "exclude_from_stats": True,
        "exclude_from_budget": True,
        "attachments": attachments or [],
    }


def _build_flow_item(tx_sync_id: str, payload: dict) -> dict:
    """用 snapshot_mutator 规范化一笔流水 item,并保留给定 syncId(原地更新用)。"""
    from ..snapshot_mutator import create_transaction as _mutate_create_tx

    _snap, _ = _mutate_create_tx({"items": [], "count": 0}, payload)
    item = dict(_snap["items"][0])
    item["syncId"] = tx_sync_id
    return item


def _create_loan_flow_tx(
    db: Session, *, ledger: Ledger, user: User, payload: dict
) -> str:
    """生成一笔真实交易流水(不在此 commit,由调用方统一提交)。返回新交易 syncId。"""
    from ..snapshot_mutator import create_transaction as _mutate_create_tx

    _snap, tx_id = _mutate_create_tx({"items": [], "count": 0}, payload)
    item = _snap["items"][0]
    now = datetime.now(timezone.utc)
    change_row = SyncChange(
        user_id=ledger.user_id,
        ledger_id=ledger.id,
        scope="ledger",
        entity_type="transaction",
        entity_sync_id=tx_id,
        action="upsert",
        payload_json=item,
        updated_at=now,
        updated_by_device_id=_LOAN_FLOW_DEVICE_ID,
        updated_by_user_id=user.id,
    )
    db.add(change_row)
    db.flush()
    upsert_tx(
        db,
        ledger_id=ledger.id,
        user_id=ledger.user_id,
        source_change_id=change_row.change_id,
        payload=item,
    )
    return tx_id


def _update_loan_flow_tx(
    db: Session, *, ledger: Ledger, user: User, tx_sync_id: str, payload: dict
) -> str:
    """原地更新一笔已有流水(保留 syncId),刷新其 SyncChange 与投影;返回同一 syncId。"""
    item = _build_flow_item(tx_sync_id, payload)
    now = datetime.now(timezone.utc)
    change = db.scalar(
        select(SyncChange)
        .where(
            SyncChange.ledger_id == ledger.id,
            SyncChange.entity_type == "transaction",
            SyncChange.entity_sync_id == tx_sync_id,
        )
        .order_by(SyncChange.change_id.desc())
    )
    if change is None:
        return _create_loan_flow_tx(db, ledger=ledger, user=user, payload=payload)
    change.payload_json = item
    change.updated_at = now
    change.updated_by_user_id = user.id
    db.flush()
    upsert_tx(
        db,
        ledger_id=ledger.id,
        user_id=ledger.user_id,
        source_change_id=change.change_id,
        payload=item,
    )
    return tx_sync_id


def _delete_loan_flow_tx(db: Session, *, ledger: Ledger, tx_sync_id: Optional[str]) -> None:
    """删除一笔借款/还款流水(连同其 SyncChange)。不在此 commit。"""
    if not tx_sync_id:
        return
    delete_tx(db, ledger_id=ledger.id, sync_id=tx_sync_id)
    db.execute(
        delete(SyncChange).where(
            SyncChange.ledger_id == ledger.id,
            SyncChange.entity_type == "transaction",
            SyncChange.entity_sync_id == tx_sync_id,
        )
    )


def _regenerate_loan_flow(
    db: Session, *, ledger: Ledger, user: User, loan: Loan, contact_name: Optional[str]
) -> None:
    """按当前借款状态生成本金流水;已有则原地更新(保留 tx_sync_id),否则新建。"""
    account = _require_account_or_404(db, user, loan.account_id)
    payload = _build_loan_flow_payload(
        direction=loan.direction,
        is_repayment=False,
        amount=loan.amount,
        currency_code=loan.currency_code,
        happened_at=loan.happened_at,
        account_id=account.sync_id,
        account_name=account.name,
        contact_name=contact_name,
        note=loan.note,
        attachments=_attachment_refs_from_ids(
            db, user_id=user.id, file_ids=_json_loads_safe(loan.voucher_file_ids_json)
        ),
    )
    if loan.tx_sync_id:
        loan.tx_sync_id = _update_loan_flow_tx(
            db, ledger=ledger, user=user, tx_sync_id=loan.tx_sync_id, payload=payload
        )
    else:
        loan.tx_sync_id = _create_loan_flow_tx(db, ledger=ledger, user=user, payload=payload)
    loan.account_id = account.sync_id
    loan.account_name = account.name


def _regenerate_repayment_flow(
    db: Session,
    *,
    ledger: Ledger,
    user: User,
    repayment: LoanRepayment,
    loan: Loan,
    contact_name: Optional[str],
    principal_override: Optional[float] = None,
) -> None:
    """按当前还款状态生成还款流水;已有则原地更新(保留 tx_sync_id),否则新建。

    0041 起:**利息不单独成流水**。实际银行流水里「本金 + 利息」就是实收的一笔,
    所以流水金额 = 本金 + 利息,备注里注明「含利息 X 元」,交易列表里也只出现一条。

    本金部分>0 才生成还款流水;为 0 时(借款已还清、本次仅付利息)删除可能
    存在的旧流水,避免产生 0 元交易 —— 但此时利息部分仍要生成一条「纯利息」流水
    (本次确实收到/付出了一笔利息钱)。

    principal_override:合并还款(0039)时传入**合并后的本金总额** —— 流水代表的是
    「一次实际收付款」,金额应是整笔总额,而不是该笔借款的分摊额。
    """
    account = _require_account_or_404(db, user, repayment.account_id or loan.account_id)
    principal_amt = float(
        principal_override if principal_override is not None else (repayment.amount or 0)
    )
    interest_amt = float(getattr(repayment, "interest_amount", None) or 0)
    # 流水金额 = 本金 + 利息(利息并入本笔,不再单独成流水)。
    flow_amt = round(principal_amt + interest_amt, 2)
    if flow_amt > 0:
        payload = _build_loan_flow_payload(
            direction=loan.direction,
            is_repayment=True,
            amount=flow_amt,
            currency_code=loan.currency_code,
            happened_at=repayment.repaid_at,
            account_id=account.sync_id,
            account_name=account.name,
            contact_name=contact_name,
            note=repayment.note,
            attachments=_attachment_refs_from_ids(
                db, user_id=user.id, file_ids=_json_loads_safe(repayment.attachment_file_ids_json)
            ),
            interest=interest_amt if interest_amt > 0 else None,
        )
        if repayment.tx_sync_id:
            repayment.tx_sync_id = _update_loan_flow_tx(
                db, ledger=ledger, user=user, tx_sync_id=repayment.tx_sync_id, payload=payload
            )
        else:
            repayment.tx_sync_id = _create_loan_flow_tx(db, ledger=ledger, user=user, payload=payload)
    else:
        if repayment.tx_sync_id:
            _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=repayment.tx_sync_id)
        repayment.tx_sync_id = None
    repayment.account_id = account.sync_id
    repayment.account_name = account.name

    # 0041:利息不再单独生成流水。历史数据里若残留独立的「借款利息」流水,一并删除,
    # 避免交易列表出现与实际不符的两条记录。
    if getattr(repayment, "interest_tx_sync_id", None):
        _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=repayment.interest_tx_sync_id)
        repayment.interest_tx_sync_id = None
    if interest_amt <= 0:
        repayment.interest_amount = None
        repayment.interest_account_id = None
        repayment.interest_account_name = None


def _require_ledger(db: Session, user: User, ledger_id: str) -> Ledger:
    ledger = db.scalar(
        select(Ledger).where(
            (Ledger.id == ledger_id) | (Ledger.external_id == ledger_id),
            Ledger.user_id == user.id,
        )
    )
    if ledger is None:
        raise HTTPException(status_code=404, detail="Ledger not found")
    return ledger


def _ledger_id_filter_values(db: Session, user: User, ledger_id: str) -> list[str]:
    """查询过滤用:请求可能传公开 id(external_id)或内部 id,而 loans.ledger_id
    存的是内部 id(历史数据可能存过原始请求值)。返回两者,兼容所有存量形态。"""
    ledger = db.scalar(
        select(Ledger).where(
            (Ledger.id == ledger_id) | (Ledger.external_id == ledger_id),
            Ledger.user_id == user.id,
        )
    )
    if ledger is None or ledger.id == ledger_id:
        return [ledger_id]
    return [ledger.id, ledger_id]


# --------------------------------------------------------------------------
# Contacts
# --------------------------------------------------------------------------


@contacts_router.get("", response_model=list[ContactOut])
def list_contacts(
    search: Optional[str] = Query(default=None, max_length=128),
    state: Literal["active", "archived", "disabled", "all"] = Query(default="active"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ContactOut]:
    """联系人列表。

    state:
    - active(默认)  未归档(含已停用,前端给已停用加标记)
    - archived      已归档(可从这里恢复)
    - disabled      未归档且已停用(可从这里重新启用)
    - all           全部
    """
    stmt = select(Contact).where(Contact.user_id == current_user.id)
    if state == "active":
        stmt = stmt.where(Contact.is_archived.is_(False))
    elif state == "archived":
        stmt = stmt.where(Contact.is_archived.is_(True))
    elif state == "disabled":
        stmt = stmt.where(Contact.is_archived.is_(False), Contact.is_active.is_(False))
    if search:
        stmt = stmt.where(Contact.name.ilike(f"%{search.strip()}%"))
    stmt = stmt.order_by(Contact.is_archived.asc(), Contact.is_active.desc(), Contact.name.asc())
    return list(db.scalars(stmt).all())


@contacts_router.post("", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
def create_contact(
    req: ContactCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Contact:
    contact = Contact(user_id=current_user.id, **req.model_dump())
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


@contacts_router.get("/suggest", response_model=list[SuggestContactOut])
def suggest_contacts_from_transactions(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SuggestContactOut]:
    """从历史交易备注里挖掘可能的"交易对手"姓名,作为一键添加联系人的候选。"""
    existing = {
        (name or "").strip().lower()
        for name in db.scalars(
            select(Contact.name).where(Contact.user_id == current_user.id)
        ).all()
    }
    notes = db.scalars(
        select(ReadTxProjection.note)
        .where(
            ReadTxProjection.user_id == current_user.id,
            ReadTxProjection.note.isnot(None),
            func.length(ReadTxProjection.note) <= 20,
        )
    ).all()
    seen: set[str] = set()
    out: list[SuggestContactOut] = []
    for raw in notes:
        name = (raw or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in existing or key in seen:
            continue
        seen.add(key)
        out.append(SuggestContactOut(name=name, source="transaction_note"))
        if len(out) >= limit:
            break
    return out


@contacts_router.get("/loan-ranks", response_model=list[ContactLoanRankOut])
def contact_loan_ranks(
    ledger_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ContactLoanRankOut]:
    """联系人借款排行(0031)。

    联系人是 user-global 实体,所以默认跨**全部账本**聚合(与联系人详情的
    summary 同口径),避免联系人页为了排行对 N 个联系人各发一次 summary 请求。
    可选传 ledger_id 只看当前账本;口径仍与 /contacts/{id}/summary 一致:
    逐笔按 repayments 汇总判定结清后累加应收/应付,借出/借入互不抵消。

    只返回有借款记录的联系人;无借款的联系人由前端按 0 展示。
    """
    stmt = select(Loan).where(
        Loan.user_id == current_user.id,
        Loan.contact_id.isnot(None),
    )
    if ledger_id:
        _require_ledger(db, current_user, ledger_id)
        stmt = stmt.where(Loan.ledger_id.in_(_ledger_id_filter_values(db, current_user, ledger_id)))
    loans = list(db.scalars(stmt).all())

    loan_ids = [loan.id for loan in loans]
    rep_map: dict[str, float] = {}
    if loan_ids:
        rows = db.execute(
            select(
                LoanRepayment.loan_id,
                func.coalesce(func.sum(LoanRepayment.amount), 0.0),
            )
            .where(LoanRepayment.loan_id.in_(loan_ids))
            .group_by(LoanRepayment.loan_id)
        ).all()
        rep_map = {loan_id: float(total) for loan_id, total in rows}

    now = datetime.now(timezone.utc)
    acc: dict[str, dict] = {}
    for loan in loans:
        contact_id = loan.contact_id or ""
        slot = acc.setdefault(
            contact_id,
            {
                "loan_count": 0,
                "lend_total": 0.0,
                "borrow_total": 0.0,
                "receivable": 0.0,
                "payable": 0.0,
            },
        )
        repaid = rep_map.get(loan.id, 0.0)
        slot["loan_count"] += 1
        if loan.direction == "lend":
            slot["lend_total"] += loan.amount
            if _compute_status(loan.amount, repaid, loan.due_at, now) != "settled":
                slot["receivable"] += max(0.0, loan.amount - repaid)
        else:
            slot["borrow_total"] += loan.amount
            if _compute_status(loan.amount, repaid, loan.due_at, now) != "settled":
                slot["payable"] += max(0.0, loan.amount - repaid)

    contact_map: dict[str, Contact] = {}
    if acc:
        contact_map = {
            c.id: c
            for c in db.scalars(
                select(Contact).where(
                    Contact.id.in_(list(acc.keys())),
                    Contact.user_id == current_user.id,
                    # 归档/停用不进排行榜;历史借款统计本身仍在详情里可查。
                    Contact.is_archived.is_(False),
                    Contact.is_active.is_(True),
                )
            ).all()
        }

    out = [
        ContactLoanRankOut(
            contact_id=contact_id,
            name=(contact_map.get(contact_id).name if contact_map.get(contact_id) else ""),
            contact_tag=(contact_map.get(contact_id).contact_tag if contact_map.get(contact_id) else None),
            **slot,
        )
        for contact_id, slot in acc.items()
    ]
    # 前端只取 top N,但后端也按「往来总额」降序输出,便于其它端复用。
    out.sort(
        key=lambda row: (
            -(row.lend_total + row.borrow_total),
            -row.lend_total,
            row.name,
        )
    )
    return out


@contacts_router.get("/{contact_id}", response_model=ContactOut)
def get_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Contact:
    return _get_contact_or_404(db, current_user, contact_id)


@contacts_router.patch("/{contact_id}", response_model=ContactOut)
def update_contact(
    contact_id: str,
    req: ContactUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Contact:
    contact = _get_contact_or_404(db, current_user, contact_id)
    data = req.model_dump(exclude_unset=True)
    # 显式传 profile: null 表示「清空资料」。模型层把 None 当作「未提供」
    # (保护老客户端只传 phone 的场景),这里归一化成空对象让 setter 一并
    # 清掉 profile_json 与旧单值列。
    if "profile" in data and data["profile"] is None:
        data["profile"] = {}
    for key, value in data.items():
        setattr(contact, key, value)
    db.commit()
    db.refresh(contact)
    return contact


def _set_contact_flag(
    db: Session, user: User, contact_id: str, field: str, value: bool
) -> Contact:
    """归档/停用状态切换的公共实现。重复调用幂等,不产生多余变更。"""
    contact = _get_contact_or_404(db, user, contact_id)
    if getattr(contact, field) != value:
        setattr(contact, field, value)
        db.commit()
        db.refresh(contact)
    return contact


@contacts_router.post("/{contact_id}/archive", response_model=ContactOut)
def archive_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Contact:
    """归档:数据完整保留,但从默认列表/借款建议/排行榜隐藏。"""
    return _set_contact_flag(db, current_user, contact_id, "is_archived", True)


@contacts_router.post("/{contact_id}/unarchive", response_model=ContactOut)
def unarchive_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Contact:
    """恢复归档:重新出现在默认列表。"""
    return _set_contact_flag(db, current_user, contact_id, "is_archived", False)


@contacts_router.post("/{contact_id}/disable", response_model=ContactOut)
def disable_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Contact:
    """停用:列表仍可见(带标记),但禁止新增借款与新关联引用。"""
    return _set_contact_flag(db, current_user, contact_id, "is_active", False)


@contacts_router.post("/{contact_id}/enable", response_model=ContactOut)
def enable_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Contact:
    """重新启用:恢复新增借款/关联能力。"""
    return _set_contact_flag(db, current_user, contact_id, "is_active", True)


@contacts_router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    contact = _get_contact_or_404(db, current_user, contact_id)
    loan_count = db.scalar(
        select(func.count(Loan.id)).where(
            Loan.contact_id == contact.id, Loan.user_id == current_user.id
        )
    )
    if loan_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="联系人有关联借款,无法删除",
        )
    db.delete(contact)
    db.commit()


@contacts_router.get("/{contact_id}/summary", response_model=ContactSummaryOut)
def contact_summary(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContactSummaryOut:
    contact = _get_contact_or_404(db, current_user, contact_id)
    loans = list(
        db.scalars(
            select(Loan).where(
                Loan.contact_id == contact.id, Loan.user_id == current_user.id
            )
        ).all()
    )
    loan_ids = [loan.id for loan in loans]
    rep_map: dict[str, float] = {}
    if loan_ids:
        rows = db.execute(
            select(LoanRepayment.loan_id, func.coalesce(func.sum(LoanRepayment.amount), 0.0)).where(
                LoanRepayment.loan_id.in_(loan_ids)
            ).group_by(LoanRepayment.loan_id)
        ).all()
        rep_map = {loan_id: float(total) for loan_id, total in rows}

    lend_total = 0.0
    borrow_total = 0.0
    receivable = 0.0
    payable = 0.0
    now = datetime.now(timezone.utc)
    for loan in loans:
        repaid = rep_map.get(loan.id, 0.0)
        if loan.direction == "lend":
            lend_total += loan.amount
            if _compute_status(loan.amount, repaid, loan.due_at, now) != "settled":
                receivable += max(0.0, loan.amount - repaid)
        else:
            borrow_total += loan.amount
            if _compute_status(loan.amount, repaid, loan.due_at, now) != "settled":
                payable += max(0.0, loan.amount - repaid)

    return ContactSummaryOut(
        contact=ContactOut.model_validate(contact),
        loan_count=len(loans),
        lend_total=lend_total,
        borrow_total=borrow_total,
        receivable=receivable,
        payable=payable,
    )


# --------------------------------------------------------------------------
# Loans
# --------------------------------------------------------------------------


@loans_router.get("", response_model=list[LoanOut])
def list_loans(
    ledger_id: Optional[str] = Query(default=None),
    contact_id: Optional[str] = Query(default=None),
    direction: Optional[LoanDirection] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[LoanOut]:
    if not ledger_id:
        raise HTTPException(status_code=422, detail="ledger_id is required")
    _require_ledger(db, current_user, ledger_id)
    ledger_values = _ledger_id_filter_values(db, current_user, ledger_id)
    stmt = select(Loan).where(Loan.ledger_id.in_(ledger_values), Loan.user_id == current_user.id)
    if contact_id:
        stmt = stmt.where(Loan.contact_id == contact_id)
    if direction:
        stmt = stmt.where(Loan.direction == direction)
    stmt = stmt.order_by(Loan.happened_at.desc())
    loans = list(db.scalars(stmt).all())

    contact_ids = {loan.contact_id for loan in loans if loan.contact_id}
    contact_map: dict[str, str] = {}
    if contact_ids:
        for c in db.scalars(select(Contact).where(Contact.id.in_(contact_ids))).all():
            contact_map[c.id] = c.name

    loan_ids = [loan.id for loan in loans]
    rep_map: dict[str, float] = {}
    if loan_ids:
        rows = db.execute(
            select(LoanRepayment.loan_id, func.coalesce(func.sum(LoanRepayment.amount), 0.0)).where(
                LoanRepayment.loan_id.in_(loan_ids)
            ).group_by(LoanRepayment.loan_id)
        ).all()
        rep_map = {loan_id: float(total) for loan_id, total in rows}

    # 0046:批量取关联借出流水的可读摘要(避免 N+1)。
    tx_map = _linked_tx_map(
        db,
        {loan.ledger_id for loan in loans} or None,
        [loan.tx_sync_id for loan in loans],
    )
    out = [
        _loan_out(
            loan,
            contact_map.get(loan.contact_id or ""),
            rep_map.get(loan.id, 0.0),
            [tx_map[loan.tx_sync_id]] if loan.tx_sync_id in tx_map else [],
        )
        for loan in loans
    ]
    if status_filter:
        out = [o for o in out if o.status == status_filter]
    return out


@loans_router.post("", response_model=LoanOut, status_code=status.HTTP_201_CREATED)
def create_loan(
    req: LoanCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LoanOut:
    ledger = _require_ledger(db, current_user, req.ledger_id)
    if req.contact_id:
        _require_contact_available(db, current_user, req.contact_id)
    # 账户为可选:选了账户才生成对应真实流水(影响账户余额、出现在交易列表、排除统计);
    # 不选则仅记录借款(兼容历史数据/纯台账场景),由前端表单强制要求选择。
    now = datetime.now(timezone.utc)
    loan = Loan(
        user_id=current_user.id,
        # 注意:loans.ledger_id 有 FK → ledgers.id(内部 UUID)。请求里传的可能是
        # 公开 id(external_id,形如 ledger_xxx),必须存解析后的 ledger.id,
        # 否则在 PRAGMA foreign_keys=ON 的真实库上 INSERT 直接报外键违规。
        ledger_id=ledger.id,
        contact_id=req.contact_id,
        direction=req.direction,
        amount=req.amount,
        currency_code=req.currency_code,
        happened_at=req.happened_at,
        due_at=req.due_at,
        interest_type=req.interest_type,
        interest_rate=req.interest_rate,
        interest_amount=req.interest_amount,
        note=req.note,
        voucher_file_ids_json=_json_dumps(req.voucher_file_ids),
        status=_compute_status(req.amount, 0.0, req.due_at, now),  # type: ignore[arg-type]
    )
    db.add(loan)
    db.flush()  # 让 loan.id 就位,并在 commit 前生成对应流水
    contact_name = None
    if loan.contact_id:
        contact_name = db.get(Contact, loan.contact_id).name
    if req.account_id:
        loan.account_id = req.account_id
        _regenerate_loan_flow(db, ledger=ledger, user=current_user, loan=loan, contact_name=contact_name)
    db.commit()
    db.refresh(loan)
    tx_map = _linked_tx_map(db, ledger.id, [loan.tx_sync_id])
    return _loan_out(
        loan, contact_name, 0.0,
        [tx_map[loan.tx_sync_id]] if loan.tx_sync_id in tx_map else [],
    )


@loans_router.get("/virtual-accounts", response_model=VirtualAccountsOut)
def virtual_accounts(
    ledger_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VirtualAccountsOut:
    _require_ledger(db, current_user, ledger_id)
    ledger_values = _ledger_id_filter_values(db, current_user, ledger_id)
    loans = list(
        db.scalars(
            select(Loan).where(Loan.ledger_id.in_(ledger_values), Loan.user_id == current_user.id)
        ).all()
    )
    loan_ids = [loan.id for loan in loans]
    rep_map: dict[str, float] = {}
    if loan_ids:
        rows = db.execute(
            select(LoanRepayment.loan_id, func.coalesce(func.sum(LoanRepayment.amount), 0.0)).where(
                LoanRepayment.loan_id.in_(loan_ids)
            ).group_by(LoanRepayment.loan_id)
        ).all()
        rep_map = {loan_id: float(total) for loan_id, total in rows}

    receivable = 0.0
    payable = 0.0
    for loan in loans:
        outstanding = max(0.0, loan.amount - rep_map.get(loan.id, 0.0))
        if outstanding <= 0:
            continue
        if loan.direction == "lend":
            receivable += outstanding
        else:
            payable += outstanding
    return VirtualAccountsOut(ledger_id=ledger_id, receivable=receivable, payable=payable)


@loans_router.get("/reminders", response_model=RemindersOut)
def loan_reminders(
    ledger_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RemindersOut:
    stmt = select(Loan).where(Loan.user_id == current_user.id)
    if ledger_id:
        # 请求可能传公开 id,库里存内部 id —— 两者都匹配,并兜底历史原始值。
        ledger_values = _ledger_id_filter_values(db, current_user, ledger_id)
        stmt = stmt.where(Loan.ledger_id.in_(ledger_values))
    loans = list(db.scalars(stmt).all())
    loan_ids = [loan.id for loan in loans]
    rep_map: dict[str, float] = {}
    if loan_ids:
        rows = db.execute(
            select(LoanRepayment.loan_id, func.coalesce(func.sum(LoanRepayment.amount), 0.0)).where(
                LoanRepayment.loan_id.in_(loan_ids)
            ).group_by(LoanRepayment.loan_id)
        ).all()
        rep_map = {loan_id: float(total) for loan_id, total in rows}

    contact_ids = {loan.contact_id for loan in loans if loan.contact_id}
    contact_map: dict[str, str] = {}
    if contact_ids:
        for c in db.scalars(select(Contact).where(Contact.id.in_(contact_ids))).all():
            contact_map[c.id] = c.name

    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    horizon = now_ts + NEAR_DUE_DAYS * 86400
    near_due: list[ReminderItem] = []
    overdue: list[ReminderItem] = []
    for loan in loans:
        repaid = rep_map.get(loan.id, 0.0)
        if _compute_status(loan.amount, repaid, loan.due_at, now) == "settled":
            continue
        if loan.due_at is None:
            continue
        due_utc = _utc(loan.due_at)
        due_ts = due_utc.timestamp()
        days_to_due = int((due_ts - now_ts) // 86400)
        item = ReminderItem(
            loan=_loan_out(loan, contact_map.get(loan.contact_id or ""), repaid),
            contact_name=contact_map.get(loan.contact_id or ""),
            days_to_due=days_to_due,
        )
        if due_ts <= now.timestamp():
            overdue.append(item)
        elif due_ts <= horizon:
            near_due.append(item)
    overdue.sort(key=lambda x: _utc(x.loan.due_at) or now)
    near_due.sort(key=lambda x: _utc(x.loan.due_at) or now)
    return RemindersOut(near_due=near_due, overdue=overdue)


@loans_router.post(
    "/repayments/merged",
    response_model=MergedRepaymentOut,
    status_code=status.HTTP_201_CREATED,
)
def add_merged_repayment(
    req: MergedRepaymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MergedRepaymentOut:
    """一次实际收付款,可能同时结清同联系人同方向下的**多笔**借款(0039)。

    真实银行流水里一笔 170 元(张三 2025-01-01:借出 50+50+50)只对应**一条**交易,
    所以这里:
      - 本金按 FIFO 分配到各笔借款(每笔一行 loan_repayments,用于本金结算);
      - 交易流水只生成**一条**本金流水(合并后总额),利息另生成一条;
      - 合并组记录被合并的各笔借款本金流水 syncId,供交易详情「查看原单」。

    req.amount = 本次实际收付的**总额**(本金 + 利息);interest_amount 不传时
    按「总额 - 剩余本金」自动拆分。
    """
    ledger = _require_ledger(db, current_user, req.ledger_id)
    if not req.contact_id:
        raise HTTPException(status_code=400, detail="merged repayment requires a contact")

    contact = db.get(Contact, req.contact_id)
    if contact is None or contact.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Contact not found")

    loans = _person_loan_pool(
        db,
        contact_id=req.contact_id,
        direction=req.direction,
        ledger_id=ledger.id,
        user_id=current_user.id,
    )
    if not loans:
        raise HTTPException(status_code=404, detail="No loan found for contact")

    # 本金总额:未显式给利息时,按「剩余本金」自动拆分(借 500 还 510 → 本金 500 + 利息 10)。
    total_principal = round(float(req.amount), 2)
    # 该联系人同方向下**实际未结清**的本金合计 —— 本金记账的上限。
    outstanding = round(
        sum(max(0.0, float(l.amount) - _sum_repaid(db, l.id)) for l in loans), 2
    )
    explicit_interest = req.interest_amount
    if explicit_interest is not None and explicit_interest > 0:
        interest_amt = round(float(explicit_interest), 2)
        principal_amt = round(max(0.0, total_principal - interest_amt), 2)
    else:
        principal_amt = round(min(total_principal, outstanding), 2)
        interest_amt = round(max(0.0, total_principal - principal_amt), 2)
        if interest_amt < 0.005:
            interest_amt = 0.0
    # 本金不得超过实际未结清额(否则会「超还」);被压缩掉的部分归入利息,
    # 保证流水金额仍等于本次实际收付的总额。
    if principal_amt > outstanding:
        principal_amt = outstanding
        interest_amt = round(max(0.0, total_principal - principal_amt), 2)
    if interest_amt <= 0:
        interest_amt = None

    allocations = _allocate_repayment_to_loans(db, loans=loans, principal=principal_amt)
    if not allocations:
        raise HTTPException(status_code=400, detail="Nothing to repay for this contact")

    group = LoanPaymentGroup(
        user_id=current_user.id,
        ledger_id=ledger.id,
        contact_id=req.contact_id,
        direction=req.direction,
        amount=principal_amt,
        interest_amount=interest_amt,
        repaid_at=req.repaid_at,
        note=req.note,
        attachment_file_ids_json=_json_dumps(req.attachment_file_ids),
        created_at=datetime.now(timezone.utc),
    )
    db.add(group)
    db.flush()

    # 账户:优先用本次指定的账户,否则沿用第一笔借款的账户。
    account_id = req.account_id or allocations[0][0].account_id
    account_name = req.account_name or allocations[0][0].account_name
    if account_id:
        account = _require_account_or_404(db, current_user, account_id)
        account_id = account.sync_id
        account_name = account.name
    group.account_id = account_id
    group.account_name = account_name

    # 逐笔生成分摊行(本金结算口径),组内**只有第一行**持有 tx_sync_id,
    # 保证交易流水里这条实际收付款只出现一次。
    merged_from: list[str] = []
    allocation_out: list[dict] = []
    first_repayment: Optional[LoanRepayment] = None
    for loan, chunk in allocations:
        repayment = LoanRepayment(
            user_id=current_user.id,
            ledger_id=ledger.id,
            loan_id=loan.id,
            amount=chunk,
            repaid_at=req.repaid_at,
            note=req.note,
            attachment_file_ids_json=_json_dumps(req.attachment_file_ids),
            interest_amount=interest_amt if first_repayment is None else None,
            interest_account_id=(
                req.interest_account_id if (interest_amt and first_repayment is None) else None
            ),
            interest_account_name=(
                req.interest_account_name if (interest_amt and first_repayment is None) else None
            ),
            account_id=account_id,
            account_name=account_name,
            group_id=group.id,
            tx_sync_id=None,
        )
        db.add(repayment)
        db.flush()
        if first_repayment is None:
            first_repayment = repayment
        if loan.tx_sync_id:
            merged_from.append(loan.tx_sync_id)
        allocation_out.append({"loan_id": loan.id, "amount": chunk})

    # 合并后的**唯一一条**流水(金额 = 合并本金 + 利息;利息不单独成流水)。
    if first_repayment is not None:
        _regenerate_repayment_flow(
            db,
            ledger=ledger,
            user=current_user,
            repayment=first_repayment,
            loan=allocations[0][0],
            contact_name=contact.name,
            principal_override=principal_amt,
        )
        group.tx_sync_id = first_repayment.tx_sync_id
        # 0041:利息并入上面这条流水,不再有独立的「借款利息」流水。
        group.interest_tx_sync_id = None
        # 组内其余分摊行不重复生成流水:清掉它们的 tx 引用(合并流水由首行持有)。
        for repayment in db.scalars(
            select(LoanRepayment).where(
                LoanRepayment.group_id == group.id, LoanRepayment.id != first_repayment.id
            )
        ):
            repayment.tx_sync_id = None
            repayment.interest_tx_sync_id = None
            repayment.interest_amount = None
            repayment.interest_account_id = None
            repayment.interest_account_name = None

    group.merged_from_sync_ids_json = json.dumps(merged_from, ensure_ascii=False)

    for loan, _chunk in allocations:
        _recompute_loan(db, loan)

    db.commit()
    db.refresh(group)
    return MergedRepaymentOut(
        id=group.id,
        ledger_id=group.ledger_id,
        contact_id=group.contact_id,
        contact_name=contact.name,
        direction=group.direction,  # type: ignore[arg-type]
        amount=group.amount,
        interest_amount=group.interest_amount,
        repaid_at=group.repaid_at,
        note=group.note,
        account_id=group.account_id,
        account_name=group.account_name,
        tx_sync_id=group.tx_sync_id,
        interest_tx_sync_id=group.interest_tx_sync_id,
        merged_from_sync_ids=merged_from,
        allocations=allocation_out,
        created_at=group.created_at,
    )


@loans_router.get("/{loan_id}", response_model=LoanOut)
def get_loan(
    loan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LoanOut:
    loan = _get_loan_or_404(db, current_user, loan_id)
    contact_name = db.get(Contact, loan.contact_id).name if loan.contact_id else None
    tx_map = _linked_tx_map(db, loan.ledger_id, [loan.tx_sync_id])
    return _loan_out(
        loan, contact_name, _sum_repaid(db, loan.id),
        [tx_map[loan.tx_sync_id]] if loan.tx_sync_id in tx_map else [],
    )


@loans_router.patch("/{loan_id}", response_model=LoanOut)
def update_loan(
    loan_id: str,
    req: LoanUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LoanOut:
    loan = _get_loan_or_404(db, current_user, loan_id)
    ledger = _require_ledger(db, current_user, loan.ledger_id)
    # 记录改账户前的旧值,用于区分「沿用借款账户」与「用户为这笔还款单独选的账户」。
    previous_account_id = loan.account_id
    data = req.model_dump(exclude_unset=True)
    if "contact_id" in data and data["contact_id"]:
        _require_contact_available(db, current_user, data["contact_id"])
    for key, value in data.items():
        if key == "voucher_file_ids":
            loan.voucher_file_ids_json = _json_dumps(value)
        else:
            setattr(loan, key, value)

    contact_name = db.get(Contact, loan.contact_id).name if loan.contact_id else None

    # 联动维护本金流水(账户/方向/金额/备注/发生时间变化)。
    if "account_id" in data:
        if loan.account_id:
            _regenerate_loan_flow(db, ledger=ledger, user=current_user, loan=loan, contact_name=contact_name)
            # 账户变化 → 重生成还款流水(loan 的联系人/方向变化也要同步备注)。
            # 还款账户是每笔可独立选择的:显式选了别的账户的保留,不覆盖用户
            # 选择;沿用旧借款账户(或无账户)的还款才跟随新账户。
            for rp in db.scalars(
                select(LoanRepayment).where(LoanRepayment.loan_id == loan.id)
            ).all():
                if not rp.account_id or rp.account_id == previous_account_id:
                    rp.account_id = loan.account_id
                    rp.account_name = loan.account_name
                _regenerate_repayment_flow(
                    db, ledger=ledger, user=current_user, repayment=rp, loan=loan, contact_name=contact_name
                )
        else:
            # 清空账户 → 删本金流水 + 全部还款流水。
            _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=loan.tx_sync_id)
            loan.tx_sync_id = None
            loan.account_name = None
            for rp in db.scalars(
                select(LoanRepayment).where(LoanRepayment.loan_id == loan.id)
            ).all():
                _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=rp.tx_sync_id)
                rp.tx_sync_id = None
                rp.account_id = None
                rp.account_name = None
    elif any(k in data for k in ("direction", "amount", "note", "happened_at", "voucher_file_ids")) and loan.account_id:
        _regenerate_loan_flow(db, ledger=ledger, user=current_user, loan=loan, contact_name=contact_name)

    _recompute_loan(db, loan)
    db.commit()
    db.refresh(loan)
    tx_map = _linked_tx_map(db, loan.ledger_id, [loan.tx_sync_id])
    return _loan_out(
        loan, contact_name, _sum_repaid(db, loan.id),
        [tx_map[loan.tx_sync_id]] if loan.tx_sync_id in tx_map else [],
    )


@loans_router.delete("/{loan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_loan(
    loan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    loan = _get_loan_or_404(db, current_user, loan_id)
    ledger = _require_ledger(db, current_user, loan.ledger_id)
    # 先删本金流水 + 全部还款流水(含利息流水),再删借款(loan_repayments 级联删除)。
    _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=loan.tx_sync_id)
    for rp in db.scalars(
        select(LoanRepayment).where(LoanRepayment.loan_id == loan.id)
    ).all():
        _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=rp.tx_sync_id)
        # 利息流水(0033)也要删 —— 否则删掉借款后利息流水会变成孤儿。
        _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=rp.interest_tx_sync_id)
    # 合并组(0039):该组若因本次删除而不再有成员,连同组合并流水一起清理。
    _cleanup_empty_payment_groups(db, ledger=ledger, loan_id=loan.id)
    db.delete(loan)
    db.commit()


def _cleanup_empty_payment_groups(db: Session, *, ledger: Ledger, loan_id: str) -> None:
    """删除借款后,清理已无分摊成员的合并组(0039)。

    组内的合并本金流水 / 利息流水挂在组上;若组内成员全部被删,这些流水就成了
    孤儿,必须一并删除,否则交易列表里会残留「收回借款」而借款已不存在。
    """
    group_ids = {
        g for g in db.scalars(
            select(LoanRepayment.group_id).where(
                LoanRepayment.loan_id == loan_id,
                LoanRepayment.group_id.is_not(None),
            )
        ).all()
    }
    for gid in group_ids:
        remaining = db.scalar(
            select(func.count()).select_from(LoanRepayment).where(
                LoanRepayment.group_id == gid
            )
        )
        if remaining:
            continue
        group = db.get(LoanPaymentGroup, gid)
        if group is None:
            continue
        _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=group.tx_sync_id)
        _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=group.interest_tx_sync_id)
        db.delete(group)


@loans_router.get("/{loan_id}/repayments", response_model=list[RepaymentOut])
def list_repayments(
    loan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[RepaymentOut]:
    _get_loan_or_404(db, current_user, loan_id)
    rows = db.scalars(
        select(LoanRepayment)
        .where(LoanRepayment.loan_id == loan_id, LoanRepayment.user_id == current_user.id)
        .order_by(LoanRepayment.repaid_at.desc())
    ).all()
    # 合并还款(0039):补上所属合并组的信息(合计金额 / 被合并笔数 / 原单列表)。
    # 组的利息/流水挂在代表行上,这里统一补到**每一行**,前端渲染时无需再判断
    # 哪一行是代表行(只按 group_id 去重即可)。
    group_ids = {r.group_id for r in rows if r.group_id}
    groups: dict[str, LoanPaymentGroup] = {}
    group_sizes: dict[str, int] = {}
    group_interest: dict[str, float] = {}
    group_interest_tx: dict[str, str] = {}
    if group_ids:
        for g in db.scalars(
            select(LoanPaymentGroup).where(LoanPaymentGroup.id.in_(group_ids))
        ).all():
            groups[g.id] = g
            if g.interest_amount:
                group_interest[g.id] = float(g.interest_amount)
            if g.interest_tx_sync_id:
                group_interest_tx[g.id] = g.interest_tx_sync_id
        for gid, n in db.execute(
            select(LoanRepayment.group_id, func.count())
            .where(LoanRepayment.group_id.in_(group_ids))
            .group_by(LoanRepayment.group_id)
        ).all():
            group_sizes[gid] = int(n or 0)
    # 0046:批量取关联流水的可读摘要(还款流水 + 组利息流水,避免 N+1)。
    _tx_map = _linked_tx_map(
        db,
        rows[0].ledger_id if rows else "",
        [r.tx_sync_id for r in rows]
        + [group_interest_tx.get(r.group_id or "") for r in rows],
    )
    out: list[RepaymentOut] = []
    for r in rows:
        _interest_tx = getattr(r, "interest_tx_sync_id", None) or group_interest_tx.get(
            r.group_id or ""
        )
        item = _repayment_out(
            r,
            group=groups.get(r.group_id) if r.group_id else None,
            group_size=group_sizes.get(r.group_id or "", 0),
            tx_details=[_tx_map[r.tx_sync_id]] if r.tx_sync_id in _tx_map else [],
            interest_tx_details=(
                [_tx_map[_interest_tx]] if _interest_tx and _interest_tx in _tx_map else []
            ),
        )
        if r.group_id:
            # 组内利息统一透出(行内可能没挂,挂在代表行上)。
            if item.interest_amount is None and r.group_id in group_interest:
                item.interest_amount = group_interest[r.group_id]
            if item.interest_tx_sync_id is None and r.group_id in group_interest_tx:
                item.interest_tx_sync_id = group_interest_tx[r.group_id]
        out.append(item)
    return out


@loans_router.post(
    "/{loan_id}/repayments",
    response_model=RepaymentOut,
    status_code=status.HTTP_201_CREATED,
)
def add_repayment(
    loan_id: str,
    req: RepaymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RepaymentOut:
    loan = _get_loan_or_404(db, current_user, loan_id)

    # 总额拆分(用户只填还款总额,如借 500 还 510):
    #   本金部分 = min(总额, 剩余本金) —— 计入 LoanRepayment.amount 用于结清判定;
    #   利息部分 = 总额 - 本金部分 —— 写入 interest_amount,0041 起并入同一笔流水的
    #   金额并在备注注明「含利息 X 元」(不单独生成「借款利息」流水,与实际银行流水一致)。
    # 显式传 interest_amount 时以用户指定为准(本金 = 总额 - 利息),兼容高级用法。
    total_amount = float(req.amount)
    already_repaid = _sum_repaid(db, loan.id)
    remaining_principal = max(0.0, float(loan.amount) - already_repaid)
    explicit_interest = getattr(req, "interest_amount", None)

    if explicit_interest is not None and explicit_interest > 0:
        # 显式利息:本金 = 总额 - 利息;本金不得为负。
        interest_amt = round(float(explicit_interest), 2)
        principal_amt = round(max(0.0, total_amount - interest_amt), 2)
    else:
        # 自动拆分:优先冲抵本金,超出部分记为利息。
        principal_amt = round(min(total_amount, remaining_principal), 2)
        interest_amt = round(max(0.0, total_amount - principal_amt), 2)
        if interest_amt < 0.005:
            interest_amt = 0.0
    # 本金不得超过剩余未结清额(否则会「超还」);被压缩掉的部分归入利息,
    # 保证流水金额仍等于本次实际收付的总额。
    if principal_amt > remaining_principal:
        principal_amt = remaining_principal
        interest_amt = round(max(0.0, total_amount - principal_amt), 2)
    if interest_amt <= 0:
        interest_amt = None

    repayment = LoanRepayment(
        user_id=current_user.id,
        ledger_id=loan.ledger_id,
        loan_id=loan.id,
        amount=principal_amt,
        repaid_at=req.repaid_at,
        note=req.note,
        attachment_file_ids_json=_json_dumps(req.attachment_file_ids),
        interest_amount=interest_amt,
        interest_account_id=getattr(req, "interest_account_id", None) if interest_amt else None,
        interest_account_name=getattr(req, "interest_account_name", None) if interest_amt else None,
    )
    db.add(repayment)
    db.flush()
    contact_name = db.get(Contact, loan.contact_id).name if loan.contact_id else None
    # 还款流水账户:优先用本次请求指定的账户(前端可自由选择),否则沿用借款账户。
    chosen_account_id = req.account_id or loan.account_id
    ledger = _require_ledger(db, current_user, loan.ledger_id)
    if chosen_account_id:
        repayment.account_id = chosen_account_id
        repayment.account_name = req.account_name or (loan.account_name if not req.account_id else None)
        _regenerate_repayment_flow(
            db, ledger=ledger, user=current_user, repayment=repayment, loan=loan, contact_name=contact_name
        )
    else:
        # 无账户:无法生成流水,清掉利息账户引用(金额仍记录在还款行上供结算/展示)。
        repayment.interest_account_id = None
        repayment.interest_account_name = None
    _recompute_loan(db, loan)
    db.commit()
    db.refresh(repayment)
    _tx_map = _linked_tx_map(
        db,
        repayment.ledger_id,
        [repayment.tx_sync_id, getattr(repayment, "interest_tx_sync_id", None)],
    )
    return _repayment_out(
        repayment,
        tx_details=(
            [_tx_map[repayment.tx_sync_id]] if repayment.tx_sync_id in _tx_map else []
        ),
        interest_tx_details=(
            [_tx_map[repayment.interest_tx_sync_id]]
            if getattr(repayment, "interest_tx_sync_id", None) in _tx_map
            else []
        ),
    )


@loans_router.delete(
    "/{loan_id}/repayments/{repayment_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_repayment(
    loan_id: str,
    repayment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    loan = _get_loan_or_404(db, current_user, loan_id)
    repayment = db.get(LoanRepayment, repayment_id)
    if repayment is None or repayment.user_id != current_user.id or repayment.loan_id != loan.id:
        raise HTTPException(status_code=404, detail="Repayment not found")
    ledger = _require_ledger(db, current_user, loan.ledger_id)

    group_id = getattr(repayment, "group_id", None)
    if group_id:
        # 合并组:一次实际收付款 = 一条流水,删除组内任一行 = 删掉整笔收付款,
        # 组内所有分摊行 + 两条流水一并删除,受影响的每笔借款都要重算状态。
        affected = _delete_merged_repayment_member(
            db, ledger=ledger, user=current_user, group_id=group_id, repayment=repayment
        )
        db.flush()
        for lid in dict.fromkeys(affected):
            parent = db.get(Loan, lid)
            if parent is not None:
                _recompute_loan(db, parent)
    else:
        # 删除还款对应的本金流水与利息流水(0033)。
        _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=repayment.tx_sync_id)
        if getattr(repayment, "interest_tx_sync_id", None):
            _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=repayment.interest_tx_sync_id)
        db.delete(repayment)
        _recompute_loan(db, loan)
    db.commit()


def _delete_merged_repayment_member(
    db: Session,
    *,
    ledger: Ledger,
    user: User,
    group_id: str,
    repayment: LoanRepayment,
) -> list[str]:
    """删除合并组里的一个分摊行(0039),返回受影响的借款 id 列表(需重算状态)。

    语义:一次实际收付款 = 一个合并组,UI 上呈现为**一条**流水。所以删除组内
    任一行都意味着「删掉这笔实际收付款」—— 组内所有分摊行、合并本金流水、
    利息流水一并删除,涉及的每笔借款都要重算状态(回到未还/部分还款)。

    (组内除首行外的分摊行 tx_sync_id 为空,它们只是「这笔钱分摊到哪笔借款」的记录;
     从任一笔借款的还款列表里点删除,用户删的都是同一笔实际收付款。)
    """
    group = db.get(LoanPaymentGroup, group_id)
    members = list(
        db.scalars(
            select(LoanRepayment).where(LoanRepayment.group_id == group_id)
        )
    )
    affected_loans: list[str] = []

    for row in members:
        affected_loans.append(row.loan_id)
        db.delete(row)
    # 立即 flush:否则后续 _sum_repaid 的查询仍会看到未删除的行,
    # 导致借款状态重算错误(已结清的借款删掉合并还款后仍是 settled)。
    db.flush()

    # 合并本金流水 + 利息流水(挂在组上;组缺失时退回行上的引用)。
    if group is not None:
        _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=group.tx_sync_id)
        _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=group.interest_tx_sync_id)
        db.delete(group)
    else:
        for row in members:
            _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=row.tx_sync_id)
            _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=row.interest_tx_sync_id)
    return affected_loans
