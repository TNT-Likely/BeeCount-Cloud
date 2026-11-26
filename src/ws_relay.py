"""A short, indexed stream for live WebSocket frames across Cloud workers."""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, delete, func, or_, select, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from .models import Ledger, LedgerMember, WebSocketFrame
from .sync_applier import merge_with_existing_user

PAGE_SIZE = 128
MAX_FRAMES = 8192
RETENTION = timedelta(minutes=5)


class WebSocketRelay:
    def __init__(self, url: URL) -> None:
        # A request may still own its business connection while broadcasting.
        # Use a small separate pool so it never waits for its own connection.
        self.engine = create_engine(
            url, poolclass=QueuePool, pool_size=2, max_overflow=0, pool_pre_ping=True
        )

    def latest(self) -> int:
        with self.engine.connect() as connection:
            return connection.scalar(select(func.max(WebSocketFrame.id))) or 0

    def publish(self, user_id: str, payload: str, shared_owner_id: str | None = None) -> None:
        frame = WebSocketFrame.__table__
        now = datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                # A relay cursor must also follow commit order. The relay has its
                # own lock and transaction, independent from accounting writes.
                connection.execute(text("SELECT pg_advisory_xact_lock(1111704430, 2)"))
            if shared_owner_id is not None:
                # Business transactions can finish broadcasting in a different
                # order from their commits. Render a fresh existing-client frame
                # while holding the relay writer lock; later frames can only
                # observe the same or a later committed projection.
                if connection.dialect.name == "sqlite":
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                message = json.loads(payload)
                external_id = message["ledgerId"]
                with Session(bind=connection) as reader:
                    member = reader.scalar(
                        select(LedgerMember.user_id)
                        .join(Ledger, Ledger.id == LedgerMember.ledger_id)
                        .where(
                            Ledger.user_id == shared_owner_id,
                            Ledger.external_id == external_id,
                            LedgerMember.user_id == user_id,
                        )
                    )
                    if not member:
                        return
                    resource_id = message["payload"]["syncId"]
                    current = merge_with_existing_user(
                        reader, message["resourceType"], shared_owner_id, resource_id, {}
                    )
                message["action"] = "upsert" if current else "delete"
                message["payload"] = current or {"syncId": resource_id}
                payload = json.dumps(message, ensure_ascii=False, default=str)
            result = connection.execute(
                frame.insert(),
                {
                    "user_id": user_id,
                    "payload": payload,
                    "created_at": now,
                },
            )
            last_id = result.inserted_primary_key[0]
            connection.execute(
                delete(frame).where(
                    or_(
                        frame.c.id <= last_id - MAX_FRAMES,
                        frame.c.created_at < now - RETENTION,
                    )
                )
            )

    def read_after(self, cursor: int) -> list:
        frame = WebSocketFrame.__table__
        with self.engine.connect() as connection:
            return list(
                connection.execute(
                    select(frame.c.id, frame.c.user_id, frame.c.payload)
                    .where(frame.c.id > cursor)
                    .order_by(frame.c.id)
                    .limit(PAGE_SIZE)
                )
            )
