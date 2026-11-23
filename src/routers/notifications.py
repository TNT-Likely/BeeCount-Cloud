"""Personal notices derived from current data health."""

import hashlib
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_scopes
from ..models import User, UserNotificationRead
from ..security import SCOPE_WEB_READ, SCOPE_WEB_WRITE
from ..services.data_cleanup.category_orphans import list_category_parent_issues

router = APIRouter()


class CategoryNoticeItem(BaseModel):
    id: str
    name: str | None
    parent_name: str | None


class CategoryParentNotice(BaseModel):
    id: str
    type: Literal["category_missing_parent"] = "category_missing_parent"
    unread: bool = True
    count: int
    categories: list[CategoryNoticeItem]


class MarkNotificationsRead(BaseModel):
    ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        min_length=1, max_length=20
    )


def _active_notices(db: Session, user_id: str) -> list[CategoryParentNotice]:
    issues = list_category_parent_issues(db, user_id=user_id)
    if not issues:
        return []

    # 同一批异常用稳定 ID，新异常或父关系变化后可再次提醒。
    encoded = json.dumps([user_id, [tuple(issue) for issue in issues]], separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    return [
        CategoryParentNotice(
            id=f"category_missing_parent:{digest}",
            count=len(issues),
            categories=[
                CategoryNoticeItem(id=r.sync_id, name=r.name, parent_name=r.parent_name)
                for r in issues[:5]
            ],
        )
    ]


@router.get("", response_model=list[CategoryParentNotice])
def list_notifications(
    response: Response,
    _scopes: set[str] = Depends(require_scopes(SCOPE_WEB_READ)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CategoryParentNotice]:
    response.headers["Cache-Control"] = "no-store"
    notices = _active_notices(db, current_user.id)
    for notice in notices:
        notice.unread = db.get(UserNotificationRead, (current_user.id, notice.id)) is None
    return notices


@router.post("/read", status_code=204)
def mark_notifications_read(
    request: MarkNotificationsRead,
    _scopes: set[str] = Depends(require_scopes(SCOPE_WEB_WRITE)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    active_ids = {notice.id for notice in _active_notices(db, current_user.id)}
    ids = active_ids.intersection(request.ids)
    if ids:
        insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
        db.execute(
            insert(UserNotificationRead)
            .values(
                [{"user_id": current_user.id, "notification_id": notice_id} for notice_id in ids]
            )
            .on_conflict_do_nothing()
        )
        db.commit()
