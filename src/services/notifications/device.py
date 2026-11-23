from itertools import groupby

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Device, DeviceSyncFailure, User
from ..sync_health import SYNC_FAILURE_THRESHOLD, SyncFailureDetail
from .types import DeviceSyncNotice, notice_id


def device_notices(db: Session, user: User) -> list[DeviceSyncNotice]:
    rows = db.execute(
        select(Device, DeviceSyncFailure)
        .join(DeviceSyncFailure, DeviceSyncFailure.device_id == Device.id)
        .where(
            Device.user_id == user.id,
            Device.revoked_at.is_(None),
            DeviceSyncFailure.failure_count >= SYNC_FAILURE_THRESHOLD,
        )
        .order_by(Device.id, DeviceSyncFailure.direction)
    )
    notices = []
    for device, group in groupby(rows, key=lambda row: row[0]):
        streaks = [row[1] for row in group]
        notices.append(
            DeviceSyncNotice(
                id=notice_id(
                    "device_sync_failure", user.id, device.id, [s.incident_id for s in streaks]
                ),
                receipt_ids=tuple(notice_id("device_sync_failure", s.incident_id) for s in streaks),
                device_id=device.id,
                device_name=device.name,
                failures=[SyncFailureDetail.from_row(s) for s in streaks],
            )
        )
    return notices
