"""Persistent, bounded failure streaks for authenticated incremental sync."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Literal, Self, cast
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models import DeviceSyncFailure
from ..time_utils import as_utc

logger = logging.getLogger(__name__)
Direction = Literal["push", "pull"]
FailureReason = Literal["changes_rejected", "server_error"]
SYNC_FAILURE_THRESHOLD = 3


class SyncFailureDetail(BaseModel):
    direction: Direction
    count: int
    reason: FailureReason
    first_failed_at: datetime
    last_failed_at: datetime

    @classmethod
    def from_row(cls, row: DeviceSyncFailure) -> Self:
        return cls(
            direction=cast(Direction, row.direction),
            count=row.failure_count,
            reason=cast(FailureReason, row.reason),
            first_failed_at=as_utc(row.first_failed_at),
            last_failed_at=as_utc(row.last_failed_at),
        )


def record_failure(
    db: Session, device_id: str, direction: Direction, reason: FailureReason
) -> None:
    now = datetime.now(timezone.utc)
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    stmt = insert(DeviceSyncFailure).values(
        device_id=device_id,
        direction=direction,
        reason=reason,
        incident_id=str(uuid4()),
        failure_count=1,
        first_failed_at=now,
        last_failed_at=now,
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["device_id", "direction"],
            set_={
                "failure_count": DeviceSyncFailure.failure_count + 1,
                "reason": reason,
                "last_failed_at": now,
            },
        )
    )


def record_recovery(db: Session, device_id: str, direction: Direction) -> None:
    db.execute(
        delete(DeviceSyncFailure).where(
            DeviceSyncFailure.device_id == device_id,
            DeviceSyncFailure.direction == direction,
        )
    )


@contextmanager
def observe_server_errors(
    db: Session, device_id: str | None, direction: Direction
) -> Iterator[None]:
    try:
        yield
    except Exception as exc:
        if device_id and not (isinstance(exc, HTTPException) and exc.status_code < 500):
            try:
                # Discard the failed business transaction before reusing its session.
                db.rollback()
                record_failure(db, device_id, direction, "server_error")
                db.commit()
            except Exception:
                logger.exception("Could not record %s sync failure", direction)
                db.rollback()
        raise
