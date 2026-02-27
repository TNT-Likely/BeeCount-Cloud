from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user, require_any_scopes, require_scopes
from ..models import Device, RefreshToken, User
from ..schemas import DeviceOut
from ..security import SCOPE_APP_WRITE, SCOPE_OPS_WRITE

router = APIRouter()
settings = get_settings()
_DEVICE_SCOPE_DEP = (
    require_any_scopes(SCOPE_OPS_WRITE, SCOPE_APP_WRITE)
    if settings.allow_app_rw_scopes
    else require_scopes(SCOPE_OPS_WRITE)
)


@router.get("", response_model=list[DeviceOut])
def list_devices(
    view: Literal["deduped", "sessions"] = Query(default="deduped"),
    active_within_days: int = Query(default=30, ge=0),
    _scopes: set[str] = Depends(_DEVICE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DeviceOut]:
    stmt = select(Device).where(
        Device.user_id == current_user.id,
        Device.revoked_at.is_(None),
    )
    if active_within_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=active_within_days)
        stmt = stmt.where(Device.last_seen_at >= cutoff)
    rows = db.scalars(stmt.order_by(Device.last_seen_at.desc())).all()

    if view == "sessions":
        return [
            DeviceOut(
                id=d.id,
                name=d.name,
                platform=d.platform,
                app_version=d.app_version,
                os_version=d.os_version,
                device_model=d.device_model,
                last_ip=d.last_ip,
                last_seen_at=d.last_seen_at,
                created_at=d.created_at,
                session_count=1,
            )
            for d in rows
        ]

    def _norm(value: str | None) -> str:
        normalized = (value or "").strip().lower()
        return normalized or "__empty__"

    grouped: dict[tuple[str, ...], list[Device]] = defaultdict(list)
    for row in rows:
        key = (
            _norm(row.user_id),
            _norm(row.name),
            _norm(row.platform),
            _norm(row.device_model),
            _norm(row.os_version),
            _norm(row.app_version),
        )
        grouped[key].append(row)

    deduped: list[DeviceOut] = []
    for bucket in grouped.values():
        bucket.sort(key=lambda item: item.last_seen_at, reverse=True)
        primary = bucket[0]
        deduped.append(
            DeviceOut(
                id=primary.id,
                name=primary.name,
                platform=primary.platform,
                app_version=primary.app_version,
                os_version=primary.os_version,
                device_model=primary.device_model,
                last_ip=primary.last_ip,
                last_seen_at=primary.last_seen_at,
                created_at=primary.created_at,
                session_count=len(bucket),
            )
        )
    deduped.sort(key=lambda item: item.last_seen_at, reverse=True)
    return deduped


@router.post("/{device_id}/revoke")
def revoke_device(
    device_id: str,
    _scopes: set[str] = Depends(_DEVICE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    device = db.scalar(
        select(Device).where(Device.id == device_id, Device.user_id == current_user.id)
    )
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    now = datetime.now(timezone.utc)
    device.revoked_at = now

    refresh_tokens = db.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == current_user.id,
            RefreshToken.device_id == device_id,
            RefreshToken.revoked_at.is_(None),
        )
    ).all()
    for token in refresh_tokens:
        token.revoked_at = now

    db.commit()
    return {"ok": True, "device_id": device_id}
