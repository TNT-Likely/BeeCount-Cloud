"""交易流水的关联反查(0043)。

需求:所有**关联交易流水**都必须有相互关联的入口 —— 用户在交易详情里能看到
「这笔流水跟哪些业务记录有关」,并一键跳转过去查看。

本模块按流水 syncId 反查所有引用它的业务实体,组装成统一的链接列表:

| 实体 | 引用字段 | link kind |
|---|---|---|
| 借款(借出/借入) | Loan.tx_sync_id | loan |
| 借款还款(收回/偿还) | LoanRepayment.tx_sync_id | loan |
| 储蓄罐存入 | SavingContribution.tx_sync_id / receive_tx_sync_id | saving_plan |
| 物品(关联已有交易) | Item.tx_sync_id(s) | item |
| 物品流水 | ItemFlow.tx_sync_id(s) | item |
| 报销项(勾选需报销的账单) | ReimbursementItem.tx_sync_id | reimbursement |
| 报销单(报销到账流水) | Reimbursement.tx_sync_id | reimbursement |
| 定期存款 | Deposit.flow_tx_sync_id 等 | deposit |
| 联系人(借还流水的对方) | Loan.contact_id / 还款父借款.contact_id | contact |
| 成员(收支归属/分摊) | ReadTxProjection.member_sync_ids_json | member |

设计要点:
- **纯读**,不改任何数据;
- 返回结构统一(前端只认 kind + title + subtitle + target),新增实体只需加一段;
- 流水→流水的跳转(退款原单 / 合并原单)由读接口的 merged_from_sync_ids /
  refund_of_sync_id 单独提供,这里只管**流水→业务实体**。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..member_splits import decode_row as decode_member_row
from ..models import (
    Contact,
    Deposit,
    DepositInterestPayment,
    Item,
    ItemFlow,
    Loan,
    LoanPaymentGroup,
    LoanRepayment,
    ReadTxProjection,
    Reimbursement,
    ReimbursementItem,
    SavingContribution,
    SavingPlan,
    User,
    UserMemberProjection,
)

links_router = APIRouter()


def _parse_ids(raw: Optional[str]) -> list[str]:
    """解析 tx_sync_ids_json(JSON 数组)。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(data, list):
        return [str(x) for x in data if x]
    return []


def _fmt_amount(value: Any) -> str:
    v = round(float(value or 0), 2)
    return str(int(round(v))) if abs(v - round(v)) < 0.005 else f"{v:.2f}"


def _fmt_dt(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def _contact_name(db: Session, contact_id: Optional[str]) -> Optional[str]:
    if not contact_id:
        return None
    row = db.get(Contact, contact_id)
    return row.name if row is not None else None


def _collect(
    db: Session, *, user_id: str, ledger_id: str, tx_sync_id: str
) -> list[dict]:
    """反查所有引用该流水的业务实体。"""
    links: list[dict] = []
    # 联系人跳转入口(0047):借款本金/还款流水可一键跳回联系人详情弹窗。
    # 用 set 去重,保证同一笔流水只生成一个联系人入口。
    seen_contact_ids: set[str] = set()

    def add_contact_link(contact_id: Optional[str], amount: Any, when: Optional[str]) -> None:
        if not contact_id or contact_id in seen_contact_ids:
            return
        name = _contact_name(db, contact_id)
        if not name:
            return
        seen_contact_ids.add(contact_id)
        links.append({
            "kind": "contact",
            "title": f"联系人 · {name}",
            "subtitle": f"{when} · 往来明细".strip(" ·") if when else "往来明细",
            "target": {"page": "contacts", "contact_id": contact_id},
            "amount": round(float(amount or 0), 2),
        })

    # ---------- 借款本金(借出/借入) ----------
    loans = list(
        db.scalars(
            select(Loan).where(
                Loan.user_id == user_id,
                Loan.ledger_id == ledger_id,
                Loan.tx_sync_id == tx_sync_id,
            )
        ).all()
    )
    for loan in loans:
        name = _contact_name(db, loan.contact_id)
        links.append({
            "kind": "loan",
            "title": f"{name or '借款'} · {_fmt_amount(loan.amount)}",
            "subtitle": f"{_fmt_dt(loan.happened_at) or ''} · {loan.note or ''}".strip(" ·"),
            "target": {"page": "loans", "loan_id": loan.id},
            "amount": round(float(loan.amount or 0), 2),
        })
        add_contact_link(loan.contact_id, loan.amount, _fmt_dt(loan.happened_at))

    # ---------- 借款还款(收回/偿还借款) ----------
    repayments = list(
        db.scalars(
            select(LoanRepayment).where(
                LoanRepayment.user_id == user_id,
                LoanRepayment.ledger_id == ledger_id,
                LoanRepayment.tx_sync_id == tx_sync_id,
            )
        ).all()
    )
    for rp in repayments:
        parent = db.get(Loan, rp.loan_id)
        name = _contact_name(db, parent.contact_id) if parent is not None else None
        group = db.get(LoanPaymentGroup, rp.group_id) if rp.group_id else None
        subtitle = _fmt_dt(rp.repaid_at) or ""
        if group is not None:
            total = db.scalar(
                select(LoanRepayment).where(LoanRepayment.group_id == group.id)
            )
            rows = list(
                db.scalars(
                    select(LoanRepayment).where(LoanRepayment.group_id == group.id)
                ).all()
            )
            subtitle += f" · 合并 {len(rows)} 笔收款"
        links.append({
            "kind": "loan",
            "title": f"{name or '借款'} · {_fmt_amount(rp.amount)}",
            "subtitle": subtitle.strip(" ·"),
            "target": {"page": "loans", "loan_id": rp.loan_id},
            "amount": round(float(rp.amount or 0), 2),
        })
        if parent is not None:
            add_contact_link(parent.contact_id, rp.amount, _fmt_dt(rp.repaid_at))

    # ---------- 储蓄罐存入 ----------
    contribs = list(
        db.scalars(
            select(SavingContribution).where(
                SavingContribution.user_id == user_id,
                SavingContribution.ledger_id == ledger_id,
                or_(
                    SavingContribution.tx_sync_id == tx_sync_id,
                    SavingContribution.receive_tx_sync_id == tx_sync_id,
                ),
            )
        ).all()
    )
    for c in contribs:
        plan = db.get(SavingPlan, c.plan_id)
        if plan is None:
            continue
        is_receive = c.receive_tx_sync_id == tx_sync_id
        links.append({
            "kind": "saving_plan",
            "title": f"{plan.name} · {_fmt_amount(c.amount)}",
            "subtitle": f"{_fmt_dt(c.happened_at) or ''} · {'收款' if is_receive else '存入'}",
            "target": {"page": "saving-plans", "plan_id": plan.id},
            "amount": round(float(c.amount or 0), 2),
        })

    # ---------- 物品(关联已有交易) ----------
    items = list(
        db.scalars(
            select(Item).where(
                Item.user_id == user_id,
                Item.ledger_id == ledger_id,
                Item.tx_sync_id == tx_sync_id,
            )
        ).all()
    )
    for it in items:
        links.append({
            "kind": "item",
            "title": f"{it.name} · {_fmt_amount(it.price)}",
            "subtitle": f"{_fmt_dt(it.purchased_at) or ''} · 关联账单",
            "target": {"page": "items", "item_id": it.id},
            "amount": round(float(it.price or 0), 2),
        })

    # ---------- 物品流水 ----------
    item_flows = list(
        db.scalars(
            select(ItemFlow).where(
                ItemFlow.user_id == user_id,
                ItemFlow.ledger_id == ledger_id,
                ItemFlow.tx_sync_id == tx_sync_id,
            )
        ).all()
    )
    for f in item_flows:
        it = db.get(Item, f.item_id)
        if it is None:
            continue
        kind_label = "收益" if f.flow_type == "income" else "支出"
        links.append({
            "kind": "item",
            "title": f"{it.name} · {kind_label} {_fmt_amount(f.amount)}",
            "subtitle": f"{_fmt_dt(f.happened_at) or ''} · 物品流水",
            "target": {"page": "items", "item_id": it.id},
            "amount": round(float(f.amount or 0), 2),
        })

    # ---------- 报销项(勾选了「需报销」的账单) ----------
    rb_items = list(
        db.scalars(
            select(ReimbursementItem).where(
                ReimbursementItem.user_id == user_id,
                ReimbursementItem.ledger_id == ledger_id,
                ReimbursementItem.tx_sync_id == tx_sync_id,
            )
        ).all()
    )
    for ri in rb_items:
        links.append({
            "kind": "reimbursement",
            "title": f"报销项 · {_fmt_amount(ri.amount)}",
            "subtitle": f"{_fmt_dt(ri.happened_at) or ''} · {ri.company_name or ''} {ri.note or ''}".strip(" ·"),
            "target": {"page": "reimbursements", "reimbursement_item_id": ri.id},
            "amount": round(float(ri.amount or 0), 2),
        })

    # ---------- 报销单(报销到账收入流水) ----------
    rbs = list(
        db.scalars(
            select(Reimbursement).where(
                Reimbursement.user_id == user_id,
                Reimbursement.ledger_id == ledger_id,
                Reimbursement.tx_sync_id == tx_sync_id,
            )
        ).all()
    )
    for rb in rbs:
        links.append({
            "kind": "reimbursement",
            "title": f"报销单 · {_fmt_amount(rb.amount)}",
            "subtitle": f"{_fmt_dt(rb.happened_at) or ''} · {rb.company_name or ''} {rb.note or ''}".strip(" ·"),
            "target": {"page": "reimbursements", "reimbursement_id": rb.id},
            "amount": round(float(rb.amount or 0), 2),
        })

    # ---------- 成员(0024):这笔流水归属/分摊给了哪些成员 ----------
    # 成员是 user-global 实体(跟联系人一样),流水侧靠
    # member_sync_ids_json / member_splits_json 关联;这里反查成可跳转入口。
    # 注意按 ledger_id 过滤:读接口会用「内部 id + 外部 id」跑两轮 _collect,
    # 不加账本过滤会把同一笔流水算两次(链接重复)。
    tx_row = db.scalar(
        select(ReadTxProjection).where(
            ReadTxProjection.user_id == user_id,
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.sync_id == tx_sync_id,
        )
    )
    if tx_row is not None:
        member_ids, _member_names, member_splits = decode_member_row(
            tx_row.member_sync_ids_json,
            tx_row.member_names_csv,
            tx_row.member_splits_json,
        )
        if member_ids:
            split_amount_by_id = {
                s["member_id"]: float(s["amount"])
                for s in member_splits
                if s.get("member_id")
            }
            split_name_by_id = {
                s["member_id"]: str(s.get("name") or "")
                for s in member_splits
                if s.get("member_id")
            }
            when = _fmt_dt(tx_row.happened_at) or ""
            # 多成员分摊时标注「分摊归属」,单成员直接归属时标注「成员归属」。
            role_label = "分摊归属" if len(member_ids) > 1 else "成员归属"
            for member_id in member_ids:
                member_row = db.get(UserMemberProjection, (user_id, member_id))
                name = (
                    (member_row.name if member_row is not None else None)
                    or split_name_by_id.get(member_id)
                    or ""
                ).strip()
                if not name:
                    continue
                # 分摊时金额取该成员分摊到的那一份;单成员则就是整笔金额。
                share = split_amount_by_id.get(member_id, float(tx_row.amount or 0))
                links.append({
                    "kind": "member",
                    "title": f"成员 · {name}",
                    "subtitle": f"{when} · {role_label}".strip(" ·"),
                    "target": {"page": "members", "member_id": member_id},
                    "amount": round(share, 2),
                })

    # ---------- 定期存款 ----------
    deposits = list(
        db.scalars(
            select(Deposit).where(
                Deposit.user_id == user_id,
                Deposit.ledger_id == ledger_id,
                or_(
                    Deposit.flow_tx_sync_id == tx_sync_id,
                    Deposit.principal_return_tx_sync_id == tx_sync_id,
                    Deposit.interest_tx_sync_id == tx_sync_id,
                ),
            )
        ).all()
    )
    for d in deposits:
        links.append({
            "kind": "deposit",
            "title": f"{d.name} · {_fmt_amount(d.principal)}",
            "subtitle": f"{_fmt_dt(d.started_at) or ''} · 定期存款",
            "target": {"page": "deposits", "deposit_id": d.id},
            "amount": round(float(d.principal or 0), 2),
        })

    # ---------- 定期存款结息流水 ----------
    interest_payments = list(
        db.scalars(
            select(DepositInterestPayment).where(
                DepositInterestPayment.user_id == user_id,
                DepositInterestPayment.ledger_id == ledger_id,
                DepositInterestPayment.tx_sync_id == tx_sync_id,
            )
        ).all()
    )
    for pay in interest_payments:
        dep = db.get(Deposit, pay.deposit_id)
        if dep is None:
            continue
        links.append({
            "kind": "deposit",
            "title": f"{dep.name} · {_fmt_amount(pay.amount)}",
            "subtitle": f"{_fmt_dt(pay.settled_at) or ''} · 结息",
            "target": {"page": "deposits", "deposit_id": dep.id},
            "amount": round(float(pay.amount or 0), 2),
        })

    return links


@links_router.get("/transactions/{tx_sync_id}/links")
def get_transaction_links(
    tx_sync_id: str,
    ledger_id: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """按流水 syncId 反查所有关联的业务实体(0043)。

    返回 {links: [...], count: n};每项含 kind/title/subtitle/target/amount。
    ledger_id 可选:不传时按用户维度查(跨账本)。
    """
    if not tx_sync_id:
        raise HTTPException(status_code=400, detail="tx_sync_id required")
    # 账本归属:优先用请求里的 ledger_id(内部 id 或 external id 都兼容),
    # 否则跨该用户所有账本查(通常只有一条命中)。
    if ledger_id:
        from .contacts_loans import _ledger_id_filter_values

        ledger_values = _ledger_id_filter_values(db, current_user, ledger_id)
        links: list[dict] = []
        for lid in ledger_values:
            links.extend(
                _collect(db, user_id=current_user.id, ledger_id=lid, tx_sync_id=tx_sync_id)
            )
    else:
        links = _collect_any_ledger(db, user_id=current_user.id, tx_sync_id=tx_sync_id)
    return {"links": links, "count": len(links)}


def _collect_any_ledger(db: Session, *, user_id: str, tx_sync_id: str) -> list[dict]:
    """不指定账本时,遍历该用户所有账本收集关联(通常只有一条命中)。"""
    from ..models import Ledger

    ledgers = list(
        db.scalars(select(Ledger).where(Ledger.user_id == user_id)).all()
    )
    out: list[dict] = []
    for lg in ledgers:
        out.extend(
            _collect(db, user_id=user_id, ledger_id=lg.id, tx_sync_id=tx_sync_id)
        )
    return out
