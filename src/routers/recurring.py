"""Ledger-scoped recurring rule management shared by Web and mobile."""

from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..ledger_access import WRITABLE_ROLES, require_accessible_ledger_by_external_id
from ..mcp.rule_tools import RecurringRule, change_recurring_rule
from ..mcp.workflow_tools import record_out
from ..models import MCPStateRecord, User
from ..recurring_handoff import last_imported_dates
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE

router = APIRouter(prefix="/ledgers/{ledger_id}/recurring-rules")


class LocalOccurrence(BaseModel):
    sync_id: str = Field(min_length=1, max_length=255)
    date: date


class RuleChange(BaseModel):
    action: Literal["create", "update", "pause", "resume", "delete"]
    data: RecurringRule | None = None
    record_id: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    client_id: UUID | None = None
    confirm: bool = False
    migrate: bool = False
    automatic: bool = False
    consumed_through: date | None = None
    distinct: bool = False
    local_transactions: list[LocalOccurrence] = Field(default_factory=list, max_length=50000)
    reviewed_mapping: str | None = None


def last_generated_local_date(row: MCPStateRecord, imported_date: str | None) -> str | None:
    due = row.data_json.get("last_due_at")
    if not due:
        return imported_date
    zone = ZoneInfo(row.data_json["rule"]["time_zone"])
    cloud_date = datetime.fromisoformat(due).astimezone(zone).date().isoformat()
    return max(cloud_date, imported_date) if imported_date else cloud_date


@router.get("", dependencies=[Depends(require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ))])
def list_rules(
    ledger_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    ledger, role = require_accessible_ledger_by_external_id(
        db, user_id=user.id, ledger_external_id=ledger_id
    )
    rows = db.scalars(
        select(MCPStateRecord)
        .where(
            MCPStateRecord.ledger_id == ledger.id,
            MCPStateRecord.kind == "recurring_rule",
            MCPStateRecord.status != "deleted",
        )
        .order_by(MCPStateRecord.created_at, MCPStateRecord.id)
    )
    settings = get_settings()
    imported = last_imported_dates(db, ledger.id)
    return dict(
        items=[
            {
                **record_out(row),
                "can_edit": role in WRITABLE_ROLES and row.user_id == user.id,
                "last_generated_local_date": last_generated_local_date(row, imported.get(row.id)),
                "next_local": row.next_due_at.replace(tzinfo=timezone.utc)
                .astimezone(ZoneInfo(row.data_json["rule"]["time_zone"]))
                .isoformat()
                if row.next_due_at
                else None,
            }
            for row in rows
        ],
        can_create=role in WRITABLE_ROLES,
        scheduler_enabled=settings.mcp_recurring_enabled,
        time_zone=settings.scheduler_timezone or settings.cloud_timezone or "UTC",
    )


@router.post("", dependencies=[Depends(require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_WRITE))])
async def change_rule(ledger_id: str, change: RuleChange, user: User = Depends(get_current_user)):
    if change.action == "create" and not change.client_id:
        raise HTTPException(422, "client_id required")
    if change.action != "create" and not (change.record_id and change.expected_revision):
        raise HTTPException(422, "record_id and expected_revision required")
    if change.migrate and (change.action not in {"create", "update"} or not change.client_id):
        raise HTTPException(422, "A migration requires a stable client_id")
    try:
        result = await change_recurring_rule(
            user,
            change.action,
            ledger_id,
            change.data,
            str(change.record_id) if change.record_id else None,
            change.expected_revision,
            change.confirm,
            client_id=change.client_id,
            migration=change.migrate,
            automatic=change.automatic,
            distinct=change.distinct,
            local_transactions=[v.model_dump(mode="json") for v in change.local_transactions]
            if change.migrate
            else [],
            reviewed_mapping=change.reviewed_mapping,
            consumed_through=change.consumed_through.isoformat()
            if change.migrate and change.consumed_through
            else None,
        )
    except ValueError as exc:
        message = str(exc)
        code = 422
        if message.startswith(
            ("REVISION_CONFLICT", "SIMILAR_RULE", "HISTORY_CONFLICT", "Already synchronized copies")
        ):
            code = 409
        elif message in {"Record not found", "Ledger not found"}:
            code = 404
        elif message.startswith(("Only the schedule creator", "Insufficient ledger role")):
            code = 403
        raise HTTPException(code, message) from exc
    if result.get("status") == "operation_pending":
        raise HTTPException(409, "OCCURRENCE_PENDING")
    return result
