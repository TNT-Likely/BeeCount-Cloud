"""Personal notices derived from current data health."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_scopes
from ..models import User, UserNotificationRead
from ..security import SCOPE_WEB_READ, SCOPE_WEB_WRITE
from ..services.notifications import active_notices
from ..services.notifications.types import CloudNotice

router = APIRouter()


class MarkNotificationsRead(BaseModel):
    ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        min_length=1, max_length=200
    )


@router.get("", response_model=list[CloudNotice])
def list_notifications(
    response: Response,
    _scopes: set[str] = Depends(require_scopes(SCOPE_WEB_READ)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CloudNotice]:
    response.headers["Cache-Control"] = "no-store"
    notices = active_notices(db, current_user)
    keys = list({key for notice in notices for key in notice.read_keys()})
    receipts: set[str] = set()
    for start in range(0, len(keys), 500):
        receipts.update(
            db.scalars(
                select(UserNotificationRead.notification_id).where(
                    UserNotificationRead.user_id == current_user.id,
                    UserNotificationRead.notification_id.in_(keys[start : start + 500]),
                )
            )
        )
    for notice in notices:
        notice.unread = not receipts.issuperset(notice.read_keys())
    return notices


@router.post("/read", status_code=204)
def mark_notifications_read(
    request: MarkNotificationsRead,
    _scopes: set[str] = Depends(require_scopes(SCOPE_WEB_WRITE)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    requested = set(request.ids)
    ids = {
        key
        for notice in active_notices(db, current_user)
        if notice.id in requested
        for key in notice.read_keys()
    }
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
