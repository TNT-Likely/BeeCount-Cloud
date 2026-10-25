"""Persist normalized plans before writing, including times/rates and completed chunks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from ..models import MCPWriteOperation
from .query_context import fingerprint


def operation_out(row):
    results = row.results_json or []
    ids = [
        sid
        for result in results
        for sid in (
            result.get("created_sync_ids")
            or ([result["entity_id"]] if result.get("entity_id") else [])
        )
    ]
    return dict(
        operation_id=row.id,
        status=row.status,
        kind=row.kind,
        completed_steps=len(results),
        total_steps=len(row.plan_json),
        transaction_boundary="Each step commits independently; completed steps are retained on failure",
        sync_ids=ids,
        results=results,
        error=row.error_json,
    )


def find_operation(sessions, user_id, key, request):
    with sessions() as db:
        row = db.scalar(
            select(MCPWriteOperation).where(
                MCPWriteOperation.user_id == user_id, MCPWriteOperation.request_key == key
            )
        )
        if row and row.request_hash != fingerprint(request):
            raise ValueError("IDEMPOTENCY_CONFLICT: key was already used with different arguments")
        if row:
            db.expunge(row)
        return row


def save_operation(sessions, *, user_id, ledger_id, key, kind, request, steps):
    existing = find_operation(sessions, user_id, key, request)
    if existing:
        return existing
    with sessions() as db:
        row = MCPWriteOperation(
            id=str(uuid4()),
            user_id=user_id,
            ledger_id=ledger_id,
            request_key=key,
            kind=kind,
            request_hash=fingerprint(request),
            plan_json=steps,
            status="pending",
            results_json=[],
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return find_operation(sessions, user_id, key, request)
        db.refresh(row)
        db.expunge(row)
        return row


async def execute_operation(sessions, row, user, call):
    """Short DB leases prevent concurrent execution without holding locks across ASGI calls."""
    now = datetime.now(timezone.utc)
    lease = str(uuid4())
    with sessions() as db:
        changed = db.execute(
            update(MCPWriteOperation)
            .where(
                MCPWriteOperation.id == row.id,
                MCPWriteOperation.user_id == user.id,
                MCPWriteOperation.status != "completed",
                or_(MCPWriteOperation.lease_until.is_(None), MCPWriteOperation.lease_until < now),
            )
            .values(
                status="running",
                lease_id=lease,
                lease_until=now + timedelta(minutes=5),
                updated_at=now,
            )
        )
        db.commit()
        row = db.get(MCPWriteOperation, row.id)
        if not changed.rowcount:
            return operation_out(row)
        steps, results = list(row.plan_json), list(row.results_json or [])
    try:
        for index in range(len(results), len(steps)):
            step = steps[index]
            # The operation identifier is stable across retries/restarts; so are its payloads.
            result = await call(
                step["method"],
                step["path"],
                user,
                json=step["body"],
                headers={"Idempotency-Key": f"mcp:{row.id}:{index}"},
            )
            results.append(result)
            with sessions() as db:
                changed = db.execute(
                    update(MCPWriteOperation)
                    .where(
                        MCPWriteOperation.id == row.id,
                        MCPWriteOperation.lease_id == lease,
                    )
                    .values(
                        results_json=results,
                        updated_at=datetime.now(timezone.utc),
                        lease_until=datetime.now(timezone.utc) + timedelta(minutes=5),
                    )
                )
                db.commit()
                if not changed.rowcount:
                    raise RuntimeError(
                        "Operation execution lease lost; query its status before retrying"
                    )
        status, error = "completed", None
    except Exception as exc:
        status = "partial" if results else "failed"
        # Do not persist exception text containing source data or bearer credentials.
        status_code = getattr(exc, "status_code", 503)
        error = {
            "step": len(results),
            "code": getattr(exc, "error_code", type(exc).__name__),
            "retryable": status_code >= 500 or status_code in {408, 429},
        }
    with sessions() as db:
        db.execute(
            update(MCPWriteOperation)
            .where(
                MCPWriteOperation.id == row.id,
                MCPWriteOperation.lease_id == lease,
            )
            .values(
                status=status,
                error_json=error,
                lease_id=None,
                lease_until=None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        return operation_out(db.get(MCPWriteOperation, row.id))
