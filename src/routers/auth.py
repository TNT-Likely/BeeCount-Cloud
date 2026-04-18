import logging
import os
import time
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Device, RefreshToken, User
from ..schemas import (
    AuthLoginRequest,
    AuthLogoutRequest,
    AuthRefreshRequest,
    AuthRegisterRequest,
    AuthTokenResponse,
    UserOut,
)
from ..security import (
    SCOPE_APP_WRITE,
    SCOPE_OPS_WRITE,
    SCOPE_WEB_READ,
    SCOPE_WEB_WRITE,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()
_rate_limit_lock = Lock()
_rate_limit_buckets: dict[str, list[float]] = {}


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _upsert_device(
    db: Session,
    user_id: str,
    device_id: str | None,
    device_name: str | None,
    platform: str | None,
    app_version: str | None = None,
    os_version: str | None = None,
    device_model: str | None = None,
    last_ip: str | None = None,
) -> Device:
    now = datetime.now(timezone.utc)
    target_id = device_id or str(uuid4())

    device = db.scalar(
        select(Device).where(Device.id == target_id, Device.user_id == user_id)
    )
    if device is None:
        device = Device(
            id=target_id,
            user_id=user_id,
            name=device_name or "Unknown Device",
            platform=platform or "unknown",
            app_version=app_version,
            os_version=os_version,
            device_model=device_model,
            last_ip=last_ip,
            last_seen_at=now,
        )
        db.add(device)
    else:
        device.name = device_name or device.name
        device.platform = platform or device.platform
        if app_version is not None:
            device.app_version = app_version
        if os_version is not None:
            device.os_version = os_version
        if device_model is not None:
            device.device_model = device_model
        if last_ip is not None:
            device.last_ip = last_ip
        device.last_seen_at = now
        device.revoked_at = None

    return device


def _resolve_scopes(client_type: str) -> list[str]:
    if client_type == "web":
        return [SCOPE_WEB_READ, SCOPE_WEB_WRITE, SCOPE_OPS_WRITE]
    return [SCOPE_APP_WRITE]


def _apply_rate_limit(request: Request, action: str) -> None:
    if settings.app_env == "test" or os.getenv("PYTEST_CURRENT_TEST"):
        return
    client = request.client.host if request.client else "unknown"
    now_ts = time.time()
    key = f"{action}:{client}"
    with _rate_limit_lock:
        bucket = _rate_limit_buckets.get(key, [])
        window = settings.rate_limit_window_seconds
        bucket = [ts for ts in bucket if now_ts - ts < window]
        if len(bucket) >= settings.rate_limit_max_requests:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
        bucket.append(now_ts)
        _rate_limit_buckets[key] = bucket


def _issue_tokens(
    db: Session,
    user: User,
    device: Device,
    *,
    client_type: str,
    scopes: list[str] | None = None,
) -> AuthTokenResponse:
    target_scopes = scopes or _resolve_scopes(client_type)
    access_token, expires_in = create_access_token(
        user.id,
        scopes=target_scopes,
        client_type=client_type,
    )
    refresh_token, refresh_expires_at = create_refresh_token(
        user.id,
        scopes=target_scopes,
        client_type=client_type,
    )

    db.add(
        RefreshToken(
            user_id=user.id,
            device_id=device.id,
            token_hash=hash_token(refresh_token),
            expires_at=refresh_expires_at,
        )
    )

    return AuthTokenResponse(
        user=UserOut(id=user.id, email=user.email, is_admin=bool(user.is_admin)),
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        device_id=device.id,
        scopes=target_scopes,
    )


@router.post("/register", response_model=AuthTokenResponse)
def register(
    req: AuthRegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AuthTokenResponse:
    if not settings.registration_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration disabled",
        )
    _apply_rate_limit(request, "register")
    email = _normalize_email(req.email)
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")

    user = User(email=email, password_hash=hash_password(req.password))
    db.add(user)
    db.flush()

    device = _upsert_device(
        db,
        user.id,
        req.device_id,
        req.device_name,
        req.platform,
        app_version=req.app_version,
        os_version=req.os_version,
        device_model=req.device_model,
        last_ip=request.client.host if request.client else None,
    )
    token_response = _issue_tokens(db, user, device, client_type=req.client_type)
    db.commit()
    logger.info(
        "auth.register user=%s email=%s device=%s platform=%s",
        user.id,
        email,
        device.id,
        req.platform,
    )
    return token_response


@router.post("/login", response_model=AuthTokenResponse)
def login(
    req: AuthLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AuthTokenResponse:
    _apply_rate_limit(request, "login")
    email = _normalize_email(req.email)
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User disabled")

    device = _upsert_device(
        db,
        user.id,
        req.device_id,
        req.device_name,
        req.platform,
        app_version=req.app_version,
        os_version=req.os_version,
        device_model=req.device_model,
        last_ip=request.client.host if request.client else None,
    )
    token_response = _issue_tokens(db, user, device, client_type=req.client_type)
    db.commit()
    logger.info(
        "auth.login user=%s device=%s platform=%s client_type=%s",
        user.id,
        device.id,
        req.platform,
        req.client_type,
    )
    return token_response


@router.post("/refresh", response_model=AuthTokenResponse)
def refresh(
    req: AuthRefreshRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AuthTokenResponse:
    try:
        payload = decode_token(req.refresh_token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        ) from exc

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id = payload.get("sub")
    client_type = payload.get("client_type")
    if not isinstance(client_type, str) or client_type not in {"app", "web"}:
        client_type = "app"

    raw_scopes = payload.get("scopes")
    scopes: list[str] = []
    if isinstance(raw_scopes, list):
        for scope in raw_scopes:
            if isinstance(scope, str) and scope:
                scopes.append(scope)
    if not scopes:
        scopes = _resolve_scopes(client_type)
    token_h = hash_token(req.refresh_token)
    now = datetime.now(timezone.utc)

    token_row = db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_h,
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
    )
    if not token_row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired",
        )

    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.is_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User disabled")

    device = db.scalar(
        select(Device).where(
            Device.id == token_row.device_id,
            Device.user_id == user.id,
        )
    )
    if device and device.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Device revoked")
    if device is None:
        device = _upsert_device(
            db,
            user.id,
            token_row.device_id,
            "Unknown Device",
            "unknown",
            last_ip=request.client.host if request.client else None,
        )
    else:
        device.last_seen_at = now
        if request.client:
            device.last_ip = request.client.host

    token_row.revoked_at = now
    token_response = _issue_tokens(
        db,
        user,
        device,
        client_type=client_type,
        scopes=scopes,
    )
    db.commit()
    logger.info(
        "auth.refresh user=%s device=%s client_type=%s",
        user.id,
        device.id if device else None,
        client_type,
    )
    return token_response


@router.post("/logout")
def logout(
    req: AuthLogoutRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    revoked = False
    if req.refresh_token:
        token_h = hash_token(req.refresh_token)
        token_row = db.scalar(
            select(RefreshToken).where(
                RefreshToken.user_id == current_user.id,
                RefreshToken.token_hash == token_h,
                RefreshToken.revoked_at.is_(None),
            )
        )
        if token_row:
            token_row.revoked_at = datetime.now(timezone.utc)
            db.commit()
            revoked = True

    logger.info("auth.logout user=%s refresh_revoked=%s", current_user.id, revoked)
    return {"ok": True}
