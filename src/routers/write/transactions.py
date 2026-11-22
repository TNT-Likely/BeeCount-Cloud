"""Transactions write endpoints.

POST / PATCH / DELETE for /ledgers/{ledger_id}/transactions(ledgers 自身除外)。
依赖 `._shared` 里的 _commit_write / _prepare_write / normalize helper /
WRITE 响应表。Endpoint 自身只管参数校验 + mutate lambda 的构造。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from ...models import Company
from ...schemas import WriteTransactionDeleteRequest

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol
from ._shared import _tx_is_refund, _tx_has_linked_refunds  # 退款(0025)路径判定

router = APIRouter()


@router.post(
    "/ledgers/{ledger_id}/transactions",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_tx(
    ledger_id: str,
    req: WriteTransactionCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json")
    # 报销(0035)：勾选"需报销"的账单强制不记收支/不记预算，且不依赖前端传值。
    is_reimbursement = bool(payload.get("is_reimbursement"))
    if is_reimbursement:
        payload["exclude_from_stats"] = True
        payload["exclude_from_budget"] = True
        # 报销公司维度(0037)：勾选报销时公司必填，服务端校验并用档案名回填快照。
        company_id = str(payload.get("reimbursement_company_id") or "").strip()
        if not company_id:
            raise HTTPException(status_code=400, detail="报销公司必填")
        company = db.scalar(
            select(Company).where(Company.user_id == current_user.id, Company.id == company_id)
        )
        if company is None:
            raise HTTPException(status_code=404, detail="company not found")
        payload["reimbursement_company_id"] = company.id
        payload["reimbursement_company_name"] = company.name
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
    # 旧架构这里要跑 _resolve_tx_dictionary_payload 去 UserAccount/Category/Tag
    # 三张投影表里查 id / 建 row。新架构所有实体都是 snapshot 里的 syncId,
    # web UI 下拉选项也从 snapshot 读,account_id / category_id / tag_ids 直接
    # 是 syncId,不再需要任何投影表。payload 直接传给 snapshot_mutator。
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    # issue #31 A1b:单笔新建走 fast path,不再全量 build snapshot(O(账本交易数)→O(1))。
    # create 没有 diff 需求(新行即唯一变更),语义与 _commit_write 一致。
    # 但退款(refund)必须查原单,fast path 的空 snapshot 无法校验「原单存在 / 是
    # expense / 已退总额」,故退款新建回退到完整 _commit_write(会 build 全量 snapshot)。
    if str(payload.get("tx_type")) == "refund":
        def _mutate(snapshot: dict):
            from ...snapshot_mutator import create_transaction as _mutate_create_tx
            return _mutate_create_tx(snapshot, mutate_payload)
        result = await _commit_write(
            request=request,
            db=db,
            current_user=current_user,
            ledger=ledger,
            base_change_id=req.base_change_id,
            request_payload=payload,
            idempotency_key=idempotency_key,
            device_id=device_id,
            audit_action="web_tx_create",
            mutate=_mutate,
        )
    else:
        result = await _commit_create_tx_fast(
            request=request,
            db=db,
            current_user=current_user,
            ledger=ledger,
            base_change_id=req.base_change_id,
            request_payload=payload,
            idempotency_key=idempotency_key,
            device_id=device_id,
            audit_action="web_tx_create",
            mutate_payload=mutate_payload,
        )
    # 报销(0035)：交易落库成功后登记为待报销项（幂等，按 tx_sync_id 去重）
    if is_reimbursement and result.entity_id:
        from ...routers.reimbursements import register_item_from_tx
        register_item_from_tx(db, current_user, ledger, result.entity_id, payload)
    # 关联物品(0044)：新建账单时选了物品 → 该交易成为这件物品的一条流水。
    if "item_id" in payload and result.entity_id:
        try:
            from ...routers.items import link_tx_to_item
            link_tx_to_item(
                db, current_user, ledger, result.entity_id,
                str(payload.get("item_id") or "").strip() or None, payload,
            )
        except HTTPException:
            raise
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).exception(
                "link tx to item failed: %s", result.entity_id
            )
            db.rollback()
    return result


@router.patch(
    "/ledgers/{ledger_id}/transactions/{tx_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_tx(
    ledger_id: str,
    tx_id: str,
    req: WriteTransactionUpdateRequest,
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
    _assert_can_modify_entity(
        db=db,
        ledger=ledger,
        current_user=current_user,
        entity_sync_id=tx_id,
    )
    # 跟 create_tx 同样改动:account/category/tag 的 id 直接走 snapshot syncId,
    # 不再经 UserAccount 投影表。
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    # 退款(refund)校验需查原单,update fast path 的 minimal snapshot 仅含 1 条目标
    # tx、定位不到原单 → 回退完整 _commit_write。命中条件:
    #   - 本次 payload 显式把 type 改成 refund;
    #   - payload 携带 refund_of_sync_id(改关联原单);
    #   - 目标 tx 本身就是 refund(改金额等也会触发退款块校验原单)。
    if (
        str(payload.get("tx_type")) == "refund"
        or "refund_of_sync_id" in payload
        or _tx_is_refund(db, ledger.id, tx_id)
        or ("note" in payload and _tx_has_linked_refunds(db, ledger.id, tx_id))
    ):
        def _mutate(snapshot: dict):
            from ...snapshot_mutator import update_transaction as _mutate_update_tx
            next_snapshot = _mutate_update_tx(snapshot, tx_id, mutate_payload)
            return next_snapshot, tx_id
        result = await _commit_write(
            request=request,
            db=db,
            current_user=current_user,
            ledger=ledger,
            base_change_id=req.base_change_id,
            request_payload=payload,
            idempotency_key=idempotency_key,
            device_id=device_id,
            audit_action="web_tx_update",
            mutate=_mutate,
        )
    else:
        result = await _commit_write_fast_tx(
            request=request,
            db=db,
            current_user=current_user,
            ledger=ledger,
            base_change_id=req.base_change_id,
            request_payload=payload,
            idempotency_key=idempotency_key,
            device_id=device_id,
            audit_action="web_tx_update",
            tx_id=tx_id,
            mutate_payload=mutate_payload,
            action="upsert",
        )
    # 流水金额/优惠变更 → 同步所有引用该流水的业务实体(0042):
    #   借款本金/还款(含合并组)、物品流水、报销。
    # 储蓄罐金额是**实时派生**(读时从流水投影算),自动跟随,无需在此同步。
    # 交易写入已提交,同步失败不影响交易本身(静默回滚)。
    # 注:支出优惠(0023)也参与借款本金计算(本金 = 实付 + 优惠 = 实际到账),
    #     所以只改优惠同样要触发同步。
    if "amount" in payload or "discount_amount" in payload:
        try:
            from ..flow_sync import sync_referenced_entities_from_flow

            sync_referenced_entities_from_flow(
                db,
                ledger_id=ledger.id,
                tx_sync_id=tx_id,
                new_amount=payload.get("amount"),
            )
        except Exception:
            db.rollback()
    # 关联物品(0044)：编辑账单时改 item_id → 移动/解除该交易的物品流水。
    if "item_id" in payload:
        try:
            from ...routers.items import link_tx_to_item

            link_tx_to_item(
                db,
                current_user,
                ledger,
                tx_id,
                str(payload.get("item_id") or "").strip() or None,
                payload,
            )
        except HTTPException:
            raise
        except Exception:
            db.rollback()
    return result


@router.delete(
    "/ledgers/{ledger_id}/transactions/{tx_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_tx(
    ledger_id: str,
    tx_id: str,
    req: WriteTransactionDeleteRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json", exclude={"expected_revision"} if req.expected_revision is None else set())
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
    _assert_can_modify_entity(
        db=db,
        ledger=ledger,
        current_user=current_user,
        entity_sync_id=tx_id,
    )
    return await _commit_write_fast_tx(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_tx_delete",
        tx_id=tx_id,
        mutate_payload=mutate_payload,
        action="delete",
    )


