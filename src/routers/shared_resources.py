"""共享账本 Owner 资源快照 endpoint。

GET /api/v1/ledgers/{ledger_external_id}/shared-resources

返回 ledger.user_id(原始 owner)的所有 user-global 分类 / 账户 / 标签。
Editor 接受邀请后调一次,把数据落到本地 SharedLedger{Categories,Accounts,Tags}
镜像表;之后通过 WS `shared_resource_change` 事件增量更新。

详见 `.docs/shared-ledger/01-product-design.md` §7 + `04-server-details.md` §3.3。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..ledger_access import require_accessible_ledger_by_external_id
from ..models import (
    User,
    UserAccountProjection,
    UserCategoryProjection,
    UserTagProjection,
)
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ

router = APIRouter()
logger = logging.getLogger(__name__)

_READ_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ)


class SharedCategoryItem(BaseModel):
    sync_id: str
    name: str | None
    kind: str | None
    icon: str | None
    icon_type: str | None
    icon_cloud_file_id: str | None
    icon_cloud_sha256: str | None
    sort_order: int | None
    level: int | None
    parent_name: str | None
    # 共享账本二级分类父子关系的稳定 FK,跟 parent_name 并存;client 优先用
    # parent_sync_id 做父子链(同名不再歧义,父分类重命名也不需要级联子)。
    parent_sync_id: str | None = None


class SharedAccountItem(BaseModel):
    sync_id: str
    name: str | None
    account_type: str | None
    currency: str | None
    initial_balance: float | None
    note: str | None
    credit_limit: float | None
    billing_day: int | None
    payment_due_day: int | None
    bank_name: str | None
    card_last_four: str | None


class SharedTagItem(BaseModel):
    sync_id: str
    name: str | None
    color: str | None


class SharedResourcesResponse(BaseModel):
    owner_user_id: str  # 这些资源所属的 user_id(Owner)
    categories: list[SharedCategoryItem]
    accounts: list[SharedAccountItem]
    tags: list[SharedTagItem]


@router.get(
    "/ledgers/{ledger_external_id}/shared-resources",
    response_model=SharedResourcesResponse,
)
def get_shared_resources(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SharedResourcesResponse:
    ledger, _role = require_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
    )
    owner_user_id = ledger.user_id

    cats = db.scalars(
        select(UserCategoryProjection)
        .where(UserCategoryProjection.user_id == owner_user_id)
        .order_by(UserCategoryProjection.sort_order.asc().nullslast())
    ).all()
    accts = db.scalars(
        select(UserAccountProjection)
        .where(UserAccountProjection.user_id == owner_user_id)
    ).all()
    tgs = db.scalars(
        select(UserTagProjection)
        .where(UserTagProjection.user_id == owner_user_id)
    ).all()

    logger.info(
        "shared-resources.get ledger=%s owner=%s categories=%d accounts=%d tags=%d caller=%s",
        ledger_external_id, owner_user_id, len(cats), len(accts), len(tgs), current_user.id,
    )

    return SharedResourcesResponse(
        owner_user_id=owner_user_id,
        categories=[
            SharedCategoryItem(
                sync_id=c.sync_id,
                name=c.name,
                kind=c.kind,
                icon=c.icon,
                icon_type=c.icon_type,
                icon_cloud_file_id=c.icon_cloud_file_id,
                icon_cloud_sha256=c.icon_cloud_sha256,
                sort_order=c.sort_order,
                level=c.level,
                parent_name=c.parent_name,
                parent_sync_id=c.parent_sync_id,
            )
            for c in cats
        ],
        accounts=[
            SharedAccountItem(
                sync_id=a.sync_id,
                name=a.name,
                account_type=a.account_type,
                currency=a.currency,
                initial_balance=a.initial_balance,
                note=a.note,
                credit_limit=a.credit_limit,
                billing_day=a.billing_day,
                payment_due_day=a.payment_due_day,
                bank_name=a.bank_name,
                card_last_four=a.card_last_four,
            )
            for a in accts
        ],
        tags=[
            SharedTagItem(sync_id=t.sync_id, name=t.name, color=t.color)
            for t in tgs
        ],
    )
