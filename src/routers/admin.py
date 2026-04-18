import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user, require_admin_user, require_any_scopes, require_scopes
from ..ledger_access import (
    get_accessible_ledger_by_external_id,
)
from ..logging_ring import get_ring_buffer
from ..models import (
    AuditLog,
    BackupArtifact,
    BackupSnapshot,
    Device,
    Ledger,
    RefreshToken,
    SyncChange,
    User,
    UserAccount,
    UserCategory,
    UserProfile,
    UserTag,
)
from ..schemas import (
    AdminBackupArtifactOut,
    AdminBackupArtifactUploadResponse,
    AdminBackupCreateRequest,
    AdminBackupCreateResponse,
    AdminBackupRestoreRequest,
    AdminBackupRestoreResponse,
    AdminBackupUploadSnapshotRequest,
    AdminDeviceListOut,
    AdminDeviceOut,
    AdminLogEntryOut,
    AdminLogListOut,
    AdminOverviewOut,
    BackupArtifactKind,
    UserAdminCreateRequest,
    UserAdminListOut,
    UserAdminOut,
    UserAdminPasswordChangeRequest,
    UserAdminPatchRequest,
)
from ..security import SCOPE_APP_WRITE, SCOPE_OPS_WRITE, hash_password, verify_password

logger = logging.getLogger(__name__)

router = APIRouter()
_SAFE_FILE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")
_BACKUP_SCOPE_DEP = require_any_scopes(SCOPE_OPS_WRITE, SCOPE_APP_WRITE)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_file_name(raw: str, fallback: str) -> str:
    candidate = Path(raw).name.strip() if raw else ""
    if not candidate:
        candidate = fallback
    cleaned = _SAFE_FILE_NAME_RE.sub("_", candidate)
    return cleaned[:255] or fallback


def _backup_root() -> Path:
    settings = get_settings()
    root = Path(settings.backup_storage_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _artifact_payload(artifact: BackupArtifact, *, ledger_external_id: str) -> AdminBackupArtifactOut:
    metadata: dict = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
    metadata_copy = dict(metadata)
    note = metadata_copy.pop("note", None)
    if not isinstance(note, str):
        note = None
    return AdminBackupArtifactOut(
        id=artifact.id,
        ledger_id=ledger_external_id,
        kind=artifact.kind,  # type: ignore[arg-type]
        file_name=artifact.file_name,
        content_type=artifact.content_type,
        checksum=artifact.checksum_sha256,
        size=artifact.size_bytes,
        created_at=artifact.created_at,
        created_by=artifact.user_id,
        note=note,
        metadata=metadata_copy,
    )


def _normalize_snapshot_payload(payload: dict, metadata: dict) -> dict:
    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Snapshot content is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise HTTPException(status_code=400, detail="Snapshot content must be a JSON object")
        normalized = dict(payload)
    else:
        normalized = {"content": json.dumps(payload, ensure_ascii=False)}

    payload_metadata = normalized.get("metadata")
    if not isinstance(payload_metadata, dict):
        payload_metadata = {}
    payload_metadata.update(metadata)
    normalized["metadata"] = payload_metadata
    return normalized


def _profile_avatar_url(*, user_id: str, avatar_version: int | None = None) -> str:
    api_prefix = get_settings().api_prefix
    base = f"{api_prefix}/profile/avatar/{user_id}"
    if avatar_version is None:
        return base
    return f"{base}?v={avatar_version}"


def _to_user_admin_out(user: User, profile: UserProfile | None = None) -> UserAdminOut:
    display_name = profile.display_name if profile is not None else None
    avatar_version = int(profile.avatar_version or 0) if profile is not None else 0
    avatar_file_id = (profile.avatar_file_id or "").strip() if profile is not None else ""
    return UserAdminOut(
        id=user.id,
        email=user.email,
        is_admin=user.is_admin,
        is_enabled=user.is_enabled,
        created_at=user.created_at,
        display_name=display_name,
        avatar_url=_profile_avatar_url(user_id=user.id, avatar_version=avatar_version)
        if avatar_file_id
        else None,
        avatar_version=avatar_version,
    )


def _load_user_profile_map(db: Session, user_ids: list[str]) -> dict[str, UserProfile]:
    normalized_ids = [uid for uid in user_ids if uid]
    if not normalized_ids:
        return {}
    rows = db.scalars(select(UserProfile).where(UserProfile.user_id.in_(normalized_ids))).all()
    return {row.user_id: row for row in rows}


def _to_admin_device_out(device: Device, user_email: str, *, threshold: datetime) -> AdminDeviceOut:
    last_seen = device.last_seen_at
    compare_threshold = threshold
    if last_seen.tzinfo is None and threshold.tzinfo is not None:
        compare_threshold = threshold.replace(tzinfo=None)
    elif last_seen.tzinfo is not None and threshold.tzinfo is None:
        compare_threshold = threshold.replace(tzinfo=timezone.utc)
    return AdminDeviceOut(
        id=device.id,
        name=device.name,
        platform=device.platform,
        app_version=device.app_version,
        os_version=device.os_version,
        device_model=device.device_model,
        last_ip=device.last_ip,
        created_at=device.created_at,
        last_seen_at=last_seen,
        is_online=last_seen >= compare_threshold and device.revoked_at is None,
        user_id=device.user_id,
        user_email=user_email,
    )


@router.get("/users", response_model=UserAdminListOut)
def list_users(
    q: str | None = Query(default=None),
    status: Literal["enabled", "disabled", "all"] = Query(default="enabled"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _scopes: set[str] = Depends(require_scopes(SCOPE_OPS_WRITE)),
    _admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> UserAdminListOut:
    conditions = []
    if q and q.strip():
        like = f"%{q.strip()}%"
        conditions.append(or_(User.email.ilike(like), User.id.ilike(like)))
    if status == "enabled":
        conditions.append(User.is_enabled.is_(True))
    elif status == "disabled":
        conditions.append(User.is_enabled.is_(False))

    where_clause = and_(*conditions) if conditions else None
    total_query = select(func.count()).select_from(User)
    data_query = select(User).order_by(User.created_at.desc(), User.id.desc()).offset(offset).limit(limit)
    if where_clause is not None:
        total_query = total_query.where(where_clause)
        data_query = data_query.where(where_clause)

    total = int(db.scalar(total_query) or 0)
    rows = db.scalars(data_query).all()
    profile_by_user_id = _load_user_profile_map(db, [row.id for row in rows])
    return UserAdminListOut(
        total=total,
        items=[_to_user_admin_out(row, profile_by_user_id.get(row.id)) for row in rows],
    )


@router.patch("/users/{user_id}", response_model=UserAdminOut)
def patch_user(
    user_id: str,
    req: UserAdminPatchRequest,
    _scopes: set[str] = Depends(require_scopes(SCOPE_OPS_WRITE)),
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> UserAdminOut:
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    changes: dict[str, Any] = {}

    if req.email is not None and req.email != user.email:
        # 唯一性校验;数据库也有 UNIQUE 约束兜底但 400 比 IntegrityError 友好。
        clash = db.scalar(
            select(User).where(User.email == req.email, User.id != user.id)
        )
        if clash is not None:
            raise HTTPException(status_code=409, detail="Email already in use")
        user.email = req.email
        changes["email"] = req.email

    if req.is_enabled is not None and req.is_enabled != user.is_enabled:
        # admin 账号不可被禁用:单用户自部署里,禁掉 admin 等于锁死自己的
        # /admin/* 控制台。想"不再当 admin"请走 `make grant-admin` 的反向
        # 运维命令 / CLI,不从 UI 走。
        if req.is_enabled is False and user.is_admin:
            raise HTTPException(
                status_code=400, detail="Cannot disable an admin user"
            )
        user.is_enabled = req.is_enabled
        changes["isEnabled"] = req.is_enabled

    if not changes:
        # 无有效变更:直接返回,不写 AuditLog,避免噪音条目。
        profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
        return _to_user_admin_out(user, profile)

    db.add(user)
    db.add(
        AuditLog(
            user_id=admin_user.id,
            ledger_id=None,
            action="admin_user_patch",
            metadata_json={"targetUserId": user.id, **changes},
        )
    )
    db.commit()
    db.refresh(user)
    logger.info(
        "admin.user.patch actor=%s target=%s changes=%s",
        admin_user.id,
        user.id,
        sorted(changes.keys()),
    )
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    return _to_user_admin_out(user, profile)


@router.post("/users/{user_id}/password", response_model=UserAdminOut)
def change_user_password(
    user_id: str,
    req: UserAdminPasswordChangeRequest,
    _scopes: set[str] = Depends(require_scopes(SCOPE_OPS_WRITE)),
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> UserAdminOut:
    # 1) 先校验**当前 admin 自己**的密码 —— 二次验证,防止被劫持的 session 改
    #    别人密码 / UI 误点造成不可逆损失。admin_user 一定是 enabled(通过了
    #    require_admin_user dep),不用再查 is_enabled。
    if not verify_password(req.admin_password, admin_user.password_hash):
        raise HTTPException(status_code=401, detail="Admin password mismatch")

    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # 2) 更新目标用户密码,并 revoke 所有 refresh token,所有设备强制重登。
    user.password_hash = hash_password(req.new_password)
    revoked = 0
    for token_row in db.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        )
    ).all():
        token_row.revoked_at = _utcnow()
        revoked += 1

    db.add(user)
    db.add(
        AuditLog(
            user_id=admin_user.id,
            ledger_id=None,
            action="admin_user_password_change",
            metadata_json={
                "targetUserId": user.id,
                "revokedRefreshTokens": revoked,
            },
        )
    )
    db.commit()
    db.refresh(user)
    logger.info(
        "admin.user.password_change actor=%s target=%s revoked=%d",
        admin_user.id,
        user.id,
        revoked,
    )
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    return _to_user_admin_out(user, profile)


@router.delete("/users/{user_id}", response_model=UserAdminOut)
def delete_user(
    user_id: str,
    _scopes: set[str] = Depends(require_scopes(SCOPE_OPS_WRITE)),
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> UserAdminOut:
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete current admin user")
    if user.is_admin:
        # admin 账号不可被禁用 → 软删除会把 is_enabled 置 false,等同禁用,
        # 因此 admin 也不能从 UI 删。跟 patch 的约束对齐。
        raise HTTPException(status_code=400, detail="Cannot delete an admin user")

    if user.is_admin and user.is_enabled:
        remaining_enabled_admins = int(
            db.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.id != user.id,
                    User.is_admin.is_(True),
                    User.is_enabled.is_(True),
                )
            )
            or 0
        )
        if remaining_enabled_admins == 0:
            raise HTTPException(status_code=400, detail="Cannot delete last enabled admin user")

    now = _utcnow()
    user.is_enabled = False
    user.is_admin = False

    tokens = db.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        )
    ).all()
    for token in tokens:
        token.revoked_at = now

    devices = db.scalars(
        select(Device).where(
            Device.user_id == user.id,
            Device.revoked_at.is_(None),
        )
    ).all()
    for device in devices:
        device.revoked_at = now

    db.add(user)
    db.add(
        AuditLog(
            user_id=admin_user.id,
            ledger_id=None,
            action="admin_user_soft_delete",
            metadata_json={
                "targetUserId": user.id,
                "targetEmail": user.email,
                "revokedRefreshTokens": len(tokens),
                "revokedDevices": len(devices),
            },
        )
    )
    db.commit()
    db.refresh(user)
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    return _to_user_admin_out(user, profile)


@router.post("/users", response_model=UserAdminOut, status_code=201)
def create_user(
    req: UserAdminCreateRequest,
    _scopes: set[str] = Depends(require_scopes(SCOPE_OPS_WRITE)),
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> UserAdminOut:
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="User password too short")
    exists = db.scalar(select(User).where(User.email == req.email))
    if exists is not None:
        raise HTTPException(status_code=409, detail="User email exists")

    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        is_admin=bool(req.is_admin),
        is_enabled=bool(req.is_enabled),
    )
    db.add(user)
    db.flush()
    db.add(
        AuditLog(
            user_id=admin_user.id,
            ledger_id=None,
            action="admin_user_create",
            metadata_json={
                "targetUserId": user.id,
                "targetEmail": user.email,
                "isAdmin": user.is_admin,
                "isEnabled": user.is_enabled,
            },
        )
    )
    db.commit()
    db.refresh(user)
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    return _to_user_admin_out(user, profile)


@router.get("/overview", response_model=AdminOverviewOut)
def admin_overview(
    _scopes: set[str] = Depends(require_scopes(SCOPE_OPS_WRITE)),
    _admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> AdminOverviewOut:
    return AdminOverviewOut(
        users_total=int(db.scalar(select(func.count()).select_from(User)) or 0),
        users_enabled_total=int(
            db.scalar(select(func.count()).select_from(User).where(User.is_enabled.is_(True))) or 0
        ),
        ledgers_total=int(db.scalar(select(func.count()).select_from(Ledger)) or 0),
        transactions_total=int(
            db.scalar(
                select(func.count()).select_from(SyncChange).where(SyncChange.entity_type == "transaction")
            ) or 0
        ),
        accounts_total=int(
            db.scalar(
                select(func.count()).select_from(UserAccount).where(UserAccount.deleted_at.is_(None))
            )
            or 0
        ),
        categories_total=int(
            db.scalar(
                select(func.count()).select_from(UserCategory).where(UserCategory.deleted_at.is_(None))
            )
            or 0
        ),
        tags_total=int(
            db.scalar(select(func.count()).select_from(UserTag).where(UserTag.deleted_at.is_(None))) or 0
        ),
    )


@router.get("/health")
def health(
    request: Request,
    _scopes: set[str] = Depends(require_scopes(SCOPE_OPS_WRITE)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _ = current_user.id
    db.execute(text("SELECT 1"))
    ws_manager = request.app.state.ws_manager
    return {
        "status": "ok",
        "db": "connected",
        "online_ws_users": len(list(ws_manager.online_user_ids())),
        "time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/devices/online")
def online_devices(
    _scopes: set[str] = Depends(require_scopes(SCOPE_OPS_WRITE)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    threshold = datetime.now(timezone.utc) - timedelta(minutes=get_settings().device_online_window_minutes)
    rows = db.scalars(
        select(Device).where(
            Device.user_id == current_user.id,
            Device.revoked_at.is_(None),
            Device.last_seen_at >= threshold,
        )
    ).all()
    return {
        "count": len(rows),
        "devices": [
            {
                "id": d.id,
                "name": d.name,
                "platform": d.platform,
                "lastSeenAt": d.last_seen_at.isoformat(),
            }
            for d in rows
        ],
    }


@router.get("/devices", response_model=AdminDeviceListOut)
def list_devices(
    q: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    online_only: bool = Query(default=False),
    active_within_days: int = Query(default=30, ge=0, le=3650),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _scopes: set[str] = Depends(require_scopes(SCOPE_OPS_WRITE)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdminDeviceListOut:
    online_threshold = datetime.now(timezone.utc) - timedelta(
        minutes=get_settings().device_online_window_minutes
    )
    active_threshold = (
        datetime.now(timezone.utc) - timedelta(days=active_within_days)
        if active_within_days > 0
        else None
    )
    conditions: list[ColumnElement[bool]] = [Device.revoked_at.is_(None)]
    if not current_user.is_admin:
        conditions.append(Device.user_id == current_user.id)
    else:
        if user_id:
            conditions.append(Device.user_id == user_id)
    if active_threshold is not None:
        conditions.append(Device.last_seen_at >= active_threshold)
    if online_only:
        conditions.append(Device.last_seen_at >= online_threshold)
    if q and q.strip():
        like = f"%{q.strip()}%"
        conditions.append(
            or_(
                Device.name.ilike(like),
                Device.id.ilike(like),
                Device.platform.ilike(like),
                User.email.ilike(like),
            )
        )

    where_clause = and_(*conditions) if conditions else None
    total_query = select(func.count()).select_from(Device).join(User, User.id == Device.user_id)
    data_query = (
        select(Device, User.email)
        .join(User, User.id == Device.user_id)
        .order_by(Device.last_seen_at.desc(), Device.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if where_clause is not None:
        total_query = total_query.where(where_clause)
        data_query = data_query.where(where_clause)

    total = int(db.scalar(total_query) or 0)
    rows = db.execute(data_query).all()
    return AdminDeviceListOut(
        total=total,
        items=[_to_admin_device_out(device, email, threshold=online_threshold) for device, email in rows],
    )


@router.get("/sync/errors")
def sync_errors(
    _scopes: set[str] = Depends(require_scopes(SCOPE_OPS_WRITE)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.user_id == current_user.id,
            AuditLog.action.in_(["sync_error", "sync_conflict"]),
        )
        .order_by(AuditLog.id.desc())
        .limit(200)
    ).all()
    return {
        "count": len(rows),
        "items": [
            {
                "id": row.id,
                "action": row.action,
                "metadata": row.metadata_json,
                "createdAt": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


@router.post("/backups/upload-db", response_model=AdminBackupArtifactUploadResponse)
async def upload_backup_db(
    ledger_id: str = Form(...),
    file: UploadFile = File(...),
    note: str | None = Form(default=None),
    metadata: str | None = Form(default=None),
    _scopes: set[str] = Depends(_BACKUP_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdminBackupArtifactUploadResponse:
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Ledger not found")
    ledger, _ = row

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Backup file is empty")
    if len(raw) > get_settings().backup_max_upload_bytes:
        raise HTTPException(status_code=413, detail="Backup upload too large")

    metadata_obj: dict[str, object] = {}
    if metadata and metadata.strip():
        try:
            parsed = json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Backup metadata must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="Backup metadata must be a JSON object")
        metadata_obj.update({str(k): v for k, v in parsed.items()})
    if note:
        metadata_obj["note"] = note

    artifact_id = str(uuid4())
    file_name = _safe_file_name(file.filename or "", fallback="backup.sqlite3")
    target_dir = _backup_root() / ledger.external_id / "db"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{artifact_id}_{file_name}"
    target_path.write_bytes(raw)

    artifact = BackupArtifact(
        id=artifact_id,
        user_id=current_user.id,
        ledger_id=ledger.id,
        kind="db",
        file_name=file_name,
        storage_path=str(target_path),
        content_type=file.content_type,
        checksum_sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        metadata_json=metadata_obj,
        created_at=_utcnow(),
    )
    db.add(artifact)
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="backup_upload_db",
            metadata_json={
                "ledgerId": ledger.external_id,
                "artifactId": artifact.id,
                "size": artifact.size_bytes,
            },
        )
    )
    db.commit()
    db.refresh(artifact)
    payload = _artifact_payload(artifact, ledger_external_id=ledger.external_id).model_dump(mode="python")
    return AdminBackupArtifactUploadResponse(**payload, snapshot_id=None)


@router.post("/backups/upload-snapshot", response_model=AdminBackupArtifactUploadResponse)
def upload_backup_snapshot(
    req: AdminBackupUploadSnapshotRequest,
    _scopes: set[str] = Depends(_BACKUP_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdminBackupArtifactUploadResponse:
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=req.ledger_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Ledger not found")
    ledger, _ = row

    metadata_obj = {str(k): v for k, v in req.metadata.items()}
    if req.note:
        metadata_obj["note"] = req.note
    payload = _normalize_snapshot_payload(req.payload, metadata_obj)
    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    payload_bytes = payload_text.encode("utf-8")
    if len(payload_bytes) > get_settings().backup_max_upload_bytes:
        raise HTTPException(status_code=413, detail="Backup upload too large")

    artifact_id = str(uuid4())
    file_name = _safe_file_name(f"{ledger.external_id}-{artifact_id}.json", fallback="snapshot.json")
    target_dir = _backup_root() / ledger.external_id / "snapshot"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / file_name
    target_path.write_text(payload_text, encoding="utf-8")

    artifact = BackupArtifact(
        id=artifact_id,
        user_id=current_user.id,
        ledger_id=ledger.id,
        kind="snapshot",
        file_name=file_name,
        storage_path=str(target_path),
        content_type="application/json",
        checksum_sha256=hashlib.sha256(payload_bytes).hexdigest(),
        size_bytes=len(payload_bytes),
        metadata_json=metadata_obj,
        created_at=_utcnow(),
    )
    db.add(artifact)

    backup = BackupSnapshot(
        user_id=current_user.id,
        ledger_id=ledger.id,
        snapshot_json=json.dumps(payload, ensure_ascii=False),
        note=req.note,
        created_at=_utcnow(),
    )
    db.add(backup)
    db.flush()
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="backup_upload_snapshot",
            metadata_json={
                "ledgerId": ledger.external_id,
                "artifactId": artifact.id,
                "snapshotId": backup.id,
                "size": artifact.size_bytes,
            },
        )
    )
    db.commit()
    db.refresh(artifact)
    payload_out = _artifact_payload(artifact, ledger_external_id=ledger.external_id).model_dump(
        mode="python"
    )
    return AdminBackupArtifactUploadResponse(**payload_out, snapshot_id=backup.id)


@router.get("/backups/artifacts", response_model=list[AdminBackupArtifactOut])
def list_backup_artifacts(
    ledger_id: str | None = Query(default=None),
    kind: BackupArtifactKind | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _scopes: set[str] = Depends(_BACKUP_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AdminBackupArtifactOut]:
    query = (
        select(BackupArtifact, Ledger.external_id)
        .join(Ledger, Ledger.id == BackupArtifact.ledger_id)
        .where(Ledger.user_id == current_user.id)
        .order_by(BackupArtifact.created_at.desc())
        .limit(limit)
    )

    if ledger_id:
        row = get_accessible_ledger_by_external_id(
            db,
            user_id=current_user.id,
            ledger_external_id=ledger_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Ledger not found")
        ledger, _ = row
        query = query.where(BackupArtifact.ledger_id == ledger.id)
    if kind:
        query = query.where(BackupArtifact.kind == kind)

    rows = db.execute(query).all()
    return [
        _artifact_payload(artifact, ledger_external_id=ledger_external_id)
        for artifact, ledger_external_id in rows
    ]


@router.post("/backups/create", response_model=AdminBackupCreateResponse)
def create_backup(
    req: AdminBackupCreateRequest,
    _scopes: set[str] = Depends(require_scopes(SCOPE_OPS_WRITE)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdminBackupCreateResponse:
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=req.ledger_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Ledger not found")
    ledger, _ = row

    snapshot = db.scalar(
        select(SyncChange)
        .where(
            SyncChange.ledger_id == ledger.id,
            SyncChange.entity_type == "ledger_snapshot",
            SyncChange.action == "upsert",
        )
        .order_by(SyncChange.change_id.desc())
    )
    if not snapshot:
        raise HTTPException(status_code=404, detail="No snapshot for ledger")

    backup = BackupSnapshot(
        user_id=current_user.id,
        ledger_id=ledger.id,
        snapshot_json=json.dumps(snapshot.payload_json, ensure_ascii=False),
        note=req.note,
    )
    db.add(backup)
    db.flush()
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="backup_create",
            metadata_json={"ledgerId": req.ledger_id, "snapshotId": backup.id},
        )
    )
    db.commit()
    db.refresh(backup)
    return AdminBackupCreateResponse(
        snapshot_id=backup.id,
        ledger_id=req.ledger_id,
        created_at=backup.created_at,
    )


@router.post("/backups/restore", response_model=AdminBackupRestoreResponse)
async def restore_backup(
    req: AdminBackupRestoreRequest,
    request: Request,
    _scopes: set[str] = Depends(require_scopes(SCOPE_OPS_WRITE)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdminBackupRestoreResponse:
    backup = db.scalar(select(BackupSnapshot).where(BackupSnapshot.id == req.snapshot_id))
    if not backup:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    ledger = db.scalar(select(Ledger).where(Ledger.id == backup.ledger_id))
    if not ledger:
        raise HTTPException(status_code=404, detail="Ledger not found")

    # Single-user-per-ledger: only the owner (or an admin) may restore.
    if ledger.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=404, detail="Ledger not found")

    payload = json.loads(backup.snapshot_json)
    sync_row = SyncChange(
        user_id=current_user.id,
        ledger_id=backup.ledger_id,
        entity_type="ledger_snapshot",
        entity_sync_id="ledger_snapshot_restore",
        action="upsert",
        payload_json=payload,
        updated_at=datetime.now(timezone.utc),
        updated_by_device_id=req.device_id,
        updated_by_user_id=current_user.id,
    )
    db.add(sync_row)
    db.flush()
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=backup.ledger_id,
            action="backup_restore",
            metadata_json={"snapshotId": backup.id},
        )
    )
    db.commit()
    db.refresh(sync_row)

    # Single-user-per-ledger: notify the owner only.
    await request.app.state.ws_manager.broadcast_to_user(
        ledger.user_id,
        {
            "type": "backup_restore",
            "serverCursor": sync_row.change_id,
            "serverTimestamp": sync_row.updated_at.isoformat(),
        },
    )

    return AdminBackupRestoreResponse(
        restored=True,
        ledger_id=ledger.external_id,
        change_id=sync_row.change_id,
    )


@router.get("/logs", response_model=AdminLogListOut)
def read_logs(
    level: str | None = Query(default=None, description="最小日志级别,DEBUG/INFO/WARNING/ERROR"),
    q: str | None = Query(default=None, description="关键词过滤(message / logger 子串匹配)"),
    source: str | None = Query(
        default=None,
        description="日志类型过滤,logger 名称前缀;多个用逗号分隔(如 'src.routers.sync,uvicorn')",
    ),
    limit: int = Query(default=200, ge=1, le=1000),
    since_seq: int | None = Query(default=None, ge=0, description="只拉 seq > since_seq 的条目,用于轮询增量"),
    _scopes: set[str] = Depends(require_scopes(SCOPE_OPS_WRITE)),
    _admin_user: User = Depends(require_admin_user),
) -> AdminLogListOut:
    buffer = get_ring_buffer()
    if buffer is None:
        return AdminLogListOut(items=[], capacity=0, latest_seq=0)
    source_list = [s for s in (source or "").split(",") if s.strip()] or None
    items = buffer.snapshot(limit=limit, level=level, q=q, since_seq=since_seq, sources=source_list)
    latest_seq = items[-1]["seq"] if items else (since_seq or 0)
    return AdminLogListOut(
        items=[AdminLogEntryOut(**entry) for entry in items],
        capacity=buffer.capacity,
        latest_seq=latest_seq,
    )


@router.get("/debug/snapshot/{ledger_external_id}")
def debug_snapshot(
    ledger_external_id: str,
    recent_changes: int = Query(default=20, ge=1, le=100),
    _scopes: set[str] = Depends(require_scopes(SCOPE_OPS_WRITE)),
    _admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Diagnostic endpoint: inspect the latest snapshot and recent SyncChanges for a ledger."""
    ledger = db.scalar(select(Ledger).where(Ledger.external_id == ledger_external_id))
    if ledger is None:
        raise HTTPException(status_code=404, detail="Ledger not found")

    # Latest snapshot
    snapshot_row = db.scalar(
        select(SyncChange)
        .where(
            SyncChange.ledger_id == ledger.id,
            SyncChange.entity_type == "ledger_snapshot",
        )
        .order_by(SyncChange.change_id.desc())
        .limit(1)
    )

    snapshot_info: dict[str, Any] = {"exists": False}
    if snapshot_row is not None:
        payload = snapshot_row.payload_json
        if isinstance(payload, str):
            payload = json.loads(payload)

        raw_json = json.dumps(payload, ensure_ascii=False) if payload else ""
        content_snapshot: dict[str, Any] = {}
        if isinstance(payload, dict):
            content = payload.get("content")
            if isinstance(content, str) and content.strip():
                try:
                    content_snapshot = json.loads(content)
                except json.JSONDecodeError:
                    content_snapshot = {}
            metadata = payload.get("metadata")
        else:
            metadata = None

        snapshot_info = {
            "exists": True,
            "change_id": snapshot_row.change_id,
            "updated_at": snapshot_row.updated_at.isoformat() if snapshot_row.updated_at else None,
            "source": metadata.get("source") if isinstance(metadata, dict) else None,
            "entity_counts": {
                "accounts": len(content_snapshot.get("accounts", [])) if isinstance(content_snapshot.get("accounts"), list) else 0,
                "categories": len(content_snapshot.get("categories", [])) if isinstance(content_snapshot.get("categories"), list) else 0,
                "tags": len(content_snapshot.get("tags", [])) if isinstance(content_snapshot.get("tags"), list) else 0,
                "items": len(content_snapshot.get("items", [])) if isinstance(content_snapshot.get("items"), list) else 0,
            },
            "raw_preview": raw_json[:500] if raw_json else "",
        }

    # Recent SyncChanges
    recent_rows = db.scalars(
        select(SyncChange)
        .where(SyncChange.ledger_id == ledger.id)
        .order_by(SyncChange.change_id.desc())
        .limit(recent_changes)
    ).all()

    entity_type_counts: dict[str, int] = {}
    recent_list: list[dict[str, Any]] = []
    for row in recent_rows:
        entity_type_counts[row.entity_type] = entity_type_counts.get(row.entity_type, 0) + 1
        recent_list.append({
            "change_id": row.change_id,
            "entity_type": row.entity_type,
            "entity_sync_id": row.entity_sync_id,
            "action": row.action,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "updated_by_device_id": row.updated_by_device_id,
        })

    return {
        "ledger_id": ledger.external_id,
        "ledger_internal_id": ledger.id,
        "snapshot": snapshot_info,
        "recent_changes": {
            "count": len(recent_list),
            "entity_type_distribution": entity_type_counts,
            "items": recent_list,
        },
    }
