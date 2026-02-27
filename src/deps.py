from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .ledger_access import get_accessible_ledger_by_external_id
from .models import Ledger, LedgerMember, User
from .security import decode_token

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
    ) -> tuple[Ledger, LedgerMember]:
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
    return user


def require_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return current_user
