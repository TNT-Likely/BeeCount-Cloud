from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .database import SessionLocal, get_db
from .ledger_access import get_accessible_ledger_by_external_id
from .models import Device, Ledger, PersonalAccessToken, User
from .security import (
    PAT_PREFIX,
    decode_token,
    looks_like_pat,
    verify_pat_hash,
)

# device.last_seen_at bump 节流 —— 高频 pull / 轮询的场景下,每请求都写 DB
# 会吵。60s 内同一 device_id 最多 bump 一次。进程级内存字典(多 worker 各自
# 一份,不互通 —— 代价是分布式场景下多写几次,但远比每请求一次少)。
_BUMP_CACHE: dict[str, datetime] = {}
_BUMP_LOCK = Lock()
_BUMP_THRESHOLD = timedelta(seconds=60)


def _bump_device_last_seen(device_id: str, user_id: str) -> None:
    """在独立 session 更新 Device.last_seen_at,**不动请求主事务**,失败静默。
    调用点:`get_current_user` 鉴权通过后,如果请求带了 `X-Device-ID` header。"""
    now = datetime.now(timezone.utc)
    with _BUMP_LOCK:
        last = _BUMP_CACHE.get(device_id)
        if last is not None and now - last < _BUMP_THRESHOLD:
            return
        _BUMP_CACHE[device_id] = now
    try:
        with SessionLocal() as session:
            session.execute(
                update(Device)
                .where(Device.id == device_id, Device.user_id == user_id)
                .values(last_seen_at=now)
            )
            session.commit()
    except Exception:
        # 刷活时间失败不影响请求主流程。下次请求再补。
        pass

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# PAT last_used_at bump 节流 — 跟 device.last_seen_at 同模式,避免 MCP 高频
# tool call 每次都写 DB。60s 内同一 PAT 最多 bump 一次。
_PAT_BUMP_CACHE: dict[str, datetime] = {}
_PAT_BUMP_LOCK = Lock()


def _bump_pat_last_used(pat_id: str, ip: str | None) -> None:
    """异步更新 PAT.last_used_at / last_used_ip。失败静默,不阻塞请求。"""
    now = datetime.now(timezone.utc)
    with _PAT_BUMP_LOCK:
        last = _PAT_BUMP_CACHE.get(pat_id)
        if last is not None and now - last < _BUMP_THRESHOLD:
            return
        _PAT_BUMP_CACHE[pat_id] = now
    try:
        with SessionLocal() as session:
            session.execute(
                update(PersonalAccessToken)
                .where(PersonalAccessToken.id == pat_id)
                .values(last_used_at=now, last_used_ip=ip)
            )
            session.commit()
    except Exception:
        pass


def _resolve_pat(
    token: str, request: Request, db: Session
) -> tuple[User, set[str]]:
    """校验 PAT 字符串 → 返回 (user, scopes)。失败抛 401/403。

    校验链:格式 prefix → sha256 + timing-safe compare → 未撤销 → 未过期 →
    user 还启用。命中后异步 bump last_used_at + last_used_ip。
    """
    import json as _json

    row = db.scalar(
        select(PersonalAccessToken).where(
            PersonalAccessToken.token_hash == __import__("hashlib").sha256(
                token.encode("utf-8")
            ).hexdigest()
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    # 即便 hash 命中,也做一次 hmac.compare_digest 兜底防 timing oracle
    if not verify_pat_hash(token, row.token_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if row.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")
    if row.expires_at is not None:
        # SQLite 不保留 tzinfo;Postgres 保留。统一按 UTC 比较,避免
        # TZ-aware/naive 抛 TypeError(过去 SSE handshake 500 的根因)。
        exp = row.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")

    user = db.scalar(select(User).where(User.id == row.user_id))
    if user is None or not user.is_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User disabled")

    try:
        scopes = set(_json.loads(row.scopes_json or "[]"))
    except Exception:
        scopes = set()

    # 异步 bump last_used,不阻塞主请求
    client_ip = request.client.host if request.client else None
    _bump_pat_last_used(row.id, client_ip)

    return user, scopes


def get_current_token_payload(token: str = Depends(oauth2_scheme)) -> dict:
    """解 JWT access token。PAT 路径不会走到这里 — 上层 `get_current_user`
    会判断 token 类型分流。仅当来源是 access token 时此 dep 才生效。
    """
    # PAT 不是 JWT,如果走到这里说明上层路由错了,显式 401
    if looks_like_pat(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="PAT cannot be used here",
        )
    try:
        payload = decode_token(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from exc

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    return payload


def get_current_scopes(
    request: Request,
    token: str = Depends(oauth2_scheme),
) -> set[str]:
    """取 JWT access token 的 scopes。

    **PAT 在这里被显式拒绝** — PAT 是 LLM/MCP 专用,不允许调常规 API。
    要走 MCP endpoint 请用 `get_mcp_scopes` / `get_mcp_user`。
    """
    if looks_like_pat(token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="PAT can only be used for MCP endpoints",
        )
    try:
        payload = decode_token(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    request.state.bc_jwt_payload = payload
    request.state.bc_auth_kind = "jwt"
    scopes = payload.get("scopes", [])
    if not isinstance(scopes, list):
        return set()
    return {str(scope) for scope in scopes if scope}


def get_mcp_scopes(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> set[str]:
    """MCP 专用 scope dep — **只接受 PAT,拒绝 JWT access token**。

    严格分流:MCP endpoint 用此 dep,其他 endpoint 用 `get_current_scopes`。
    PAT 跟 access token 互不相通,边界清晰。
    """
    if not looks_like_pat(token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access token cannot be used here; use a PAT",
        )
    user, scopes = _resolve_pat(token, request, db)
    request.state.bc_user = user
    request.state.bc_auth_kind = "pat"
    return scopes


def require_mcp_scopes(*required: str) -> Callable:
    """MCP endpoint 的 scope 检查 wrapper。跟 require_scopes 同模式,但绑 PAT
    路径,不会被 JWT access token 冒充通过。
    """
    required_set = set(required)

    def _dep(scopes: set[str] = Depends(get_mcp_scopes)) -> set[str]:
        if not required_set.issubset(scopes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient MCP scope",
            )
        return scopes

    return _dep


def get_mcp_user(
    request: Request,
    _scopes: set[str] = Depends(get_mcp_scopes),
) -> User:
    """MCP endpoint 拿 user — 校验 PAT 后从 request.state 直接取(已缓存)。"""
    cached = getattr(request.state, "bc_user", None)
    if not isinstance(cached, User):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthenticated")
    return cached


def require_scopes(*required: str) -> Callable:
    required_set = set(required)

    def _dep(scopes: set[str] = Depends(get_current_scopes)) -> set[str]:
        if not required_set.issubset(scopes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient scope",
            )
        return scopes

    return _dep


def require_any_scopes(*required_any: str) -> Callable:
    required_any_set = set(required_any)

    def _dep(scopes: set[str] = Depends(get_current_scopes)) -> set[str]:
        if required_any_set and required_any_set.isdisjoint(scopes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient scope",
            )
        return scopes

    return _dep


def require_ledger_role(*roles: str) -> Callable:
    role_set = set(roles)

    def _dep(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> tuple[Ledger, None]:
        ledger_external_id = (
            request.path_params.get("ledger_external_id")
            or request.path_params.get("ledger_id")
            or request.query_params.get("ledger_id")
        )
        if not ledger_external_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ledger id required")
        out = get_accessible_ledger_by_external_id(
            db,
            user_id=current_user.id,
            ledger_external_id=ledger_external_id,
            roles=role_set or None,
        )
        if out is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger not found")
        return out

    return _dep


def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """常规 endpoint 取 user。**PAT 在这里被显式拒绝** — PAT 走 `get_mcp_user`。
    """
    # PAT 不允许走常规 API
    if looks_like_pat(token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="PAT can only be used for MCP endpoints",
        )

    cached = getattr(request.state, "bc_user", None)
    if isinstance(cached, User):
        return cached

    try:
        payload = decode_token(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.is_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User disabled")
    request.state.bc_user = user
    request.state.bc_auth_kind = "jwt"

    # 每次鉴权请求 bump 对应 device 的 last_seen_at(仅 JWT 路径,PAT 不绑 device)
    device_id = request.headers.get("X-Device-ID") or request.headers.get("x-device-id")
    if device_id:
        _bump_device_last_seen(device_id.strip(), user.id)
    return user


def require_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return current_user
