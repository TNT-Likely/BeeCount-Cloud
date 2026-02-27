import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from passlib.context import CryptContext

from .config import get_settings

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
settings = get_settings()

SCOPE_APP_WRITE = "app_write"
SCOPE_WEB_READ = "web_read"
SCOPE_WEB_WRITE = "web_write"
SCOPE_OPS_WRITE = "ops_write"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_invite_code(code: str) -> str:
    return hash_token(f"invite:{code}")


def _normalize_scopes(scopes: list[str] | None) -> list[str]:
    if not scopes:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for scope in scopes:
        if not scope or scope in seen:
            continue
        seen.add(scope)
        ordered.append(scope)
    return ordered


def _create_token(
    sub: str,
    token_type: str,
    expires_delta: timedelta,
    scopes: list[str] | None = None,
    client_type: str = "app",
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "type": token_type,
        "client_type": client_type,
        "scopes": _normalize_scopes(scopes),
        "jti": uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(
    user_id: str,
    *,
    scopes: list[str] | None = None,
    client_type: str = "app",
) -> tuple[str, int]:
    minutes = settings.access_token_expire_minutes
    token = _create_token(
        user_id,
        "access",
        timedelta(minutes=minutes),
        scopes=scopes,
        client_type=client_type,
    )
    return token, minutes * 60


def create_refresh_token(
    user_id: str,
    *,
    scopes: list[str] | None = None,
    client_type: str = "app",
) -> tuple[str, datetime]:
    days = settings.refresh_token_expire_days
    expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    token = _create_token(
        user_id,
        "refresh",
        timedelta(days=days),
        scopes=scopes,
        client_type=client_type,
    )
    return token, expires_at


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
