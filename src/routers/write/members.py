"""账本内自定义成员 write 端点(0024)。

POST / PATCH / DELETE for /ledgers/{ledger_id}/members。

跟 src/routers/members.py(共享账本**登录用户**成员管理)是两套东西:这里
的 member 是账本自己维护的「人」名单(爸爸/妈妈/我),不要求对方有账号,
用途是给支出账单做**人头分摊**(tx.member_shares)并支撑成员维度统计。

实体不进 snapshot,直连投影表走 ``_commit_entity_write`` 快路径(对齐
loans.py:锁 + SyncChange + 同事务投影 upsert + 广播)。

业务校验是纯函数(validate_member):名字必填、长度、排序值。不做重名强校验
—— 重名靠 sync_id 区分,统计按 sync_id 聚合,UI 上仍可辨。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from ... import projection
from ...entity_extensions import member_to_camel, validate_member
from ...models import ReadMemberProjection
from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol

router = APIRouter()


@router.post(
    "/ledgers/{ledger_id}/members",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_member(
    ledger_id: str,
    req: WriteMemberCreateRequest,
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
    mutate_payload = member_to_camel(_payload_with_actor(payload, current_user, ledger=ledger))
    return await _commit_entity_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=mutate_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_member_create",
        entity_type="member",
        entity_id=None,
        action="upsert",
        model=ReadMemberProjection,
        upsert_fn=projection.upsert_member,
        delete_fn=projection.delete_member,
        prefix="mb",
        validate_fn=validate_member,
    )


@router.patch(
    "/ledgers/{ledger_id}/members/{member_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_member(
    ledger_id: str,
    member_id: str,
    req: WriteMemberUpdateRequest,
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
    mutate_payload = member_to_camel(_payload_with_actor(payload, current_user, ledger=ledger))
    return await _commit_entity_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=mutate_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_member_update",
        entity_type="member",
        entity_id=member_id,
        action="upsert",
        model=ReadMemberProjection,
        upsert_fn=projection.upsert_member,
        delete_fn=projection.delete_member,
        prefix="mb",
        validate_fn=validate_member,
    )


@router.delete(
    "/ledgers/{ledger_id}/members/{member_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_member(
    ledger_id: str,
    member_id: str,
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
    # 删除成员**不**清理已记账单上的 member_shares:份额里存了 memberId +
    # memberName 快照,历史账单仍按当时的人名展示,统计也不会丢这笔。
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    return await _commit_entity_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=mutate_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_member_delete",
        entity_type="member",
        entity_id=member_id,
        action="delete",
        model=ReadMemberProjection,
        upsert_fn=projection.upsert_member,
        delete_fn=projection.delete_member,
        prefix="mb",
        validate_fn=validate_member,
    )
