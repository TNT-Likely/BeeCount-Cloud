from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import cast

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("beecount.errors")

_DETAIL_ERROR_CODE_RULES: list[tuple[str, str]] = [
    ("invalid token", "AUTH_INVALID_TOKEN"),
    ("insufficient scope", "AUTH_INSUFFICIENT_SCOPE"),
    ("invalid credentials", "AUTH_INVALID_CREDENTIALS"),
    ("email already exists", "AUTH_EMAIL_EXISTS"),
    ("user email exists", "USER_EMAIL_EXISTS"),
    ("user password too short", "USER_PASSWORD_TOO_SHORT"),
    ("cannot delete current admin user", "ADMIN_USER_DELETE_SELF_FORBIDDEN"),
    ("cannot delete last enabled admin user", "ADMIN_USER_DELETE_LAST_ADMIN_FORBIDDEN"),
    ("ledger not found", "LEDGER_NOT_FOUND"),
    ("ledger already exists", "LEDGER_ALREADY_EXISTS"),
    ("ledger name is required", "WRITE_VALIDATION_FAILED"),
    ("viewer cannot push changes", "SYNC_VIEWER_WRITE_FORBIDDEN"),
    ("no write access to ledger", "SYNC_LEDGER_WRITE_FORBIDDEN"),
    ("write conflict", "WRITE_CONFLICT"),
    ("write validation failed", "WRITE_VALIDATION_FAILED"),
    ("write role forbidden", "WRITE_ROLE_FORBIDDEN"),
    ("entity not found", "ENTITY_NOT_FOUND"),
    ("idempotency key reused with different payload", "IDEMPOTENCY_KEY_REUSED"),
    ("backup file is empty", "BACKUP_FILE_EMPTY"),
    ("backup upload too large", "BACKUP_UPLOAD_TOO_LARGE"),
    ("backup metadata must be valid json", "BACKUP_METADATA_INVALID"),
    ("backup metadata must be a json object", "BACKUP_METADATA_INVALID"),
    ("snapshot content is not valid json", "BACKUP_SNAPSHOT_INVALID"),
    ("snapshot content must be a json object", "BACKUP_SNAPSHOT_INVALID"),
    ("invalid device", "DEVICE_INVALID"),
    ("too many requests", "RATE_LIMITED"),
    ("refresh token expired", "AUTH_REFRESH_EXPIRED"),
    ("admin required", "ADMIN_FORBIDDEN"),
    ("attachment upload too large", "ATTACHMENT_UPLOAD_TOO_LARGE"),
    ("attachment file is empty", "ATTACHMENT_FILE_EMPTY"),
    ("attachment access forbidden", "ATTACHMENT_ACCESS_FORBIDDEN"),
    ("attachment write forbidden", "ATTACHMENT_WRITE_FORBIDDEN"),
    ("attachment not found", "ATTACHMENT_NOT_FOUND"),
    ("attachment file missing", "ATTACHMENT_FILE_MISSING"),
    ("profile avatar upload too large", "PROFILE_AVATAR_UPLOAD_TOO_LARGE"),
    ("profile avatar file is empty", "PROFILE_AVATAR_FILE_EMPTY"),
    ("profile avatar format invalid", "PROFILE_AVATAR_FORMAT_INVALID"),
    ("profile avatar not found", "PROFILE_AVATAR_NOT_FOUND"),
]

_STATUS_ERROR_CODE_MAP: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
    status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
    status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_409_CONFLICT: "CONFLICT",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "VALIDATION_ERROR",
    status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "INTERNAL_ERROR",
}


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, set):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if isinstance(rid, str) and rid:
        return rid
    return "unknown"


def _resolve_error_code(status_code: int, detail: str) -> str:
    normalized = detail.strip().lower()
    for token, code in _DETAIL_ERROR_CODE_RULES:
        if token in normalized:
            return code
    return _STATUS_ERROR_CODE_MAP.get(status_code, "INTERNAL_ERROR")


def _error_payload(
    *,
    request: Request,
    status_code: int,
    message: str,
    detail: object | None = None,
    error_code: str | None = None,
    extra_fields: dict[str, object] | None = None,
) -> dict[str, object]:
    code = error_code or _resolve_error_code(status_code, message)
    payload: dict[str, object] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": _request_id(request),
        },
        "detail": message,
    }
    if detail is not None:
        payload["validation"] = detail
    if extra_fields:
        payload.update(extra_fields)
    return payload


def register_exception_handlers(app: FastAPI) -> None:
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        extra_fields: dict[str, object] | None = None
        if isinstance(exc.detail, dict):
            maybe_message = exc.detail.get("message")
            if isinstance(maybe_message, str) and maybe_message.strip():
                message = maybe_message
            extra_fields = {
                str(k): v for k, v in exc.detail.items() if k != "message"
            }
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(
                request=request,
                status_code=exc.status_code,
                message=message,
                extra_fields=extra_fields,
            ),
        )

    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_payload(
                request=request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                message="Request validation failed",
                detail=_json_safe(exc.errors()),
                error_code="VALIDATION_ERROR",
            ),
        )

    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception request_id=%s", _request_id(request), exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_payload(
                request=request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message="Internal server error",
                error_code="INTERNAL_ERROR",
            ),
        )

    exception_handler = cast(
        Callable[[Request, Exception], JSONResponse | Awaitable[JSONResponse]],
        _http_exception_handler,
    )
    validation_handler = cast(
        Callable[[Request, Exception], JSONResponse | Awaitable[JSONResponse]],
        _validation_exception_handler,
    )
    unknown_handler = cast(
        Callable[[Request, Exception], JSONResponse | Awaitable[JSONResponse]],
        _unhandled_exception_handler,
    )
    app.add_exception_handler(HTTPException, exception_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(Exception, unknown_handler)
