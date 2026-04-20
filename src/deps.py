from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .database import SessionLocal, get_db
from .ledger_access import get_accessible_ledger_by_external_id
from .models import Device, Ledger, User
from .security import decode_token

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


def get_current_token_payload(token: str = Depends(oauth2_scheme)) -> dict:
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


def get_current_scopes(payload: dict = Depends(get_current_token_payload)) -> set[str]:
    scopes = payload.get("scopes", [])
    if not isinstance(scopes, list):
        return set()
    return {str(scope) for scope in scopes if scope}


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
    payload: dict = Depends(get_current_token_payload),
    db: Session = Depends(get_db),
) -> User:
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.is_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User disabled")

    # 每次鉴权请求 bump 对应 device 的 last_seen_at,这样设备页的"最近活跃时间"
    # 能真实反映 device 的使用情况(之前只在 login / refresh / sync push 时才
    # 更新 —— 对 web 这种不走 sync 只走 read 的 client 等价于"上次登录时间")。
    # 走独立 session + 60s 节流,见 _bump_device_last_seen 注释。
    device_id = request.headers.get("X-Device-ID") or request.headers.get("x-device-id")
    if device_id:
        _bump_device_last_seen(device_id.strip(), user.id)
    return user


def require_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return current_user
