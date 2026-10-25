"""Shared MCP visibility, calendar boundaries and tamper-proof continuation tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select

from ..config import get_settings
from ..ledger_access import list_accessible_ledgers
from ..models import Ledger, SyncChange
from ..routers.read._shared import _is_ledger_deleted


def visible_ledgers(db, user_id):
    own = db.scalars(select(Ledger).where(Ledger.user_id == user_id)).all()
    by_id = {row.id: row for row in [*own, *list_accessible_ledgers(db, user_id=user_id)]}
    return sorted(
        (row for row in by_id.values() if not _is_ledger_deleted(db, ledger_id=row.id)),
        key=lambda row: (row.created_at, row.id),
    )


def revision(db, ledger_ids, user_id):
    return int(
        db.scalar(
            select(func.max(SyncChange.change_id)).where(
                or_(
                    SyncChange.ledger_id.in_(ledger_ids),
                    (SyncChange.user_id == user_id) & (SyncChange.scope == "user"),
                )
            )
        )
        or 0
    )


def zone(time_zone=None):
    settings = get_settings()
    return ZoneInfo(time_zone or settings.scheduler_timezone or settings.cloud_timezone or "UTC")


def date_boundary(value, *, end=False, time_zone=None):
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if len(value.strip()) == 10 and end:
        parsed += timedelta(days=1)
    if parsed.tzinfo is None:
        # Query timezone is explicitly selectable, independently of ingestion defaults.
        local_zone = zone(time_zone)
        candidates = {
            parsed.replace(tzinfo=local_zone, fold=fold).astimezone(timezone.utc)
            for fold in (0, 1)
            if parsed.replace(tzinfo=local_zone, fold=fold)
            .astimezone(timezone.utc)
            .astimezone(local_zone)
            .replace(tzinfo=None)
            == parsed
        }
        if len(candidates) != 1:
            raise ValueError(
                "Ambiguous or nonexistent query boundary; supply an explicit UTC offset"
            )
        return candidates.pop()
    return parsed.astimezone(timezone.utc)


def encode_cursor(payload):
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(get_settings().jwt_secret.encode(), data, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig + data).decode().rstrip("=")


def decode_cursor(value):
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        signature, data = raw[:32], raw[32:]
        expected = hmac.new(get_settings().jwt_secret.encode(), data, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        return json.loads(data)
    except Exception as exc:
        raise ValueError("Invalid continuation cursor") from exc


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        ).encode()
    ).hexdigest()
