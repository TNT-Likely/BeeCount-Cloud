import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ..sync_health import SyncFailureDetail


class Notice(BaseModel):
    id: str
    unread: bool = True
    # Grouped cards acknowledge each underlying incident. When one recovers,
    # the other must not become unread simply because the group got smaller.
    receipt_ids: tuple[str, ...] = Field(default=(), exclude=True)

    def read_keys(self) -> tuple[str, ...]:
        return self.receipt_ids or (self.id,)


def notice_id(kind: str, *identity: object) -> str:
    # JSON shape also preserves category receipts issued before other sources existed.
    encoded = json.dumps(identity, separators=(",", ":"))
    return f"{kind}:{hashlib.sha256(encoded.encode()).hexdigest()}"


class CategoryNoticeItem(BaseModel):
    id: str
    name: str | None
    parent_name: str | None


class CategoryParentNotice(Notice):
    type: Literal["category_missing_parent"] = "category_missing_parent"
    count: int
    categories: list[CategoryNoticeItem]


class BackupNotice(Notice):
    type: Literal["backup_schedule_issue"] = "backup_schedule_issue"
    schedule_id: int
    schedule_name: str
    reason: Literal["failed", "partial", "overdue"]
    count: int
    run_id: int | None
    at: datetime


class DeviceSyncNotice(Notice):
    type: Literal["device_sync_failure"] = "device_sync_failure"
    device_id: str
    device_name: str
    failures: list[SyncFailureDetail]


CloudNotice = Annotated[
    CategoryParentNotice | BackupNotice | DeviceSyncNotice, Field(discriminator="type")
]
