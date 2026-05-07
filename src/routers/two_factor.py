"""2FA(TOTP)端点:setup / confirm / verify / disable / regenerate / status。

设计:.docs/2fa-design.md。

login 端点的 2FA challenge 分支在 routers/auth.py 内联(因为复用 _issue_tokens
和 _upsert_device);本文件只装独立路径。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Lock
from time import time as now_ts

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Device, RecoveryCode, User
from ..schemas import (
    AuthLoginResponse,
    TwoFAConfirmRequest,
    TwoFAConfirmResponse,
    TwoFADisableRequest,
    TwoFARegenerateRequest,
    TwoFARegenerateResponse,
    TwoFASetupResponse,
    TwoFAStatusResponse,
    TwoFAVerifyRequest,
)
from ..security import (
    decode_2fa_challenge_token,
    verify_password,
)
from ..services.totp import (
    build_otpauth_uri,
    constant_time_match,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    verify_totp_code,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------- Rate limit (simple in-memory) ----------------
# /verify 是暴力破解的主要靶子,本地内存桶按 IP + challenge_token 计数。
# 部署多 worker 时不严格,但能挡 99% 的脚本撞库;真要严防需上 Redis。
_VERIFY_RATE_LIMIT_MAX = 5  # 每分钟最多 5 次失败
_VERIFY_RATE_LIMIT_WINDOW = 60.0
_verify_rate_lock = Lock()
_verify_rate_buckets: dict[str, list[float]] = {}


def _check_verify_rate_limit(request: Request, challenge_token: str) -> None:
    client = request.client.host if request.client else "unknown"
    # 同一 challenge 只接受 5 次失败,过 = 直接 429,客户端要重新 login
    key = f"{client}:{challenge_token[-32:]}"
    ts = now_ts()
    with _verify_rate_lock:
        bucket = _verify_rate_buckets.get(key, [])
        bucket = [t for t in bucket if ts - t < _VERIFY_RATE_LIMIT_WINDOW]
        if len(bucket) >= _VERIFY_RATE_LIMIT_MAX:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many 2FA verification attempts, please re-login.",
            )
        bucket.append(ts)
        _verify_rate_buckets[key] = bucket


# ---------------- Status ----------------


@router.get("/status", response_model=TwoFAStatusResponse)
def status_endpoint(
    current_user: User = Depends(get_current_user),
) -> TwoFAStatusResponse:
    return TwoFAStatusResponse(
        enabled=bool(current_user.totp_enabled),
        enabled_at=current_user.totp_enabled_at,
    )


# ---------------- Setup (Step 1: 生成 secret) ----------------


@router.post("/setup", response_model=TwoFASetupResponse)
def setup(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TwoFASetupResponse:
    """生成新的 TOTP secret 并落库(加密)。totp_enabled 仍为 False。

    重复调用 = 重新生成 secret 覆盖旧的。所以用户半路退出再来不会卡死。
    已经启用 2FA 的用户走 disable 后再来,这里直接 409 防误覆盖。
    """
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="2FA already enabled. Disable first to re-setup.",
        )

    secret = generate_totp_secret()
    current_user.totp_secret_encrypted = encrypt_totp_secret(secret)
    current_user.totp_enabled = False
    current_user.totp_enabled_at = None
    db.commit()

    qr_uri = build_otpauth_uri(secret, current_user.email)
    logger.info("2fa.setup user=%s", current_user.id)
    return TwoFASetupResponse(secret=secret, qr_code_uri=qr_uri, expires_in=300)


# ---------------- Setup (Step 2: 输 6 位码确认 → 启用 + 发 recovery codes) ----------------


@router.post("/confirm", response_model=TwoFAConfirmResponse)
def confirm(
    req: TwoFAConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TwoFAConfirmResponse:
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="2FA already enabled.",
        )
    if not current_user.totp_secret_encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending 2FA setup. Call /2fa/setup first.",
        )

    secret = decrypt_totp_secret(current_user.totp_secret_encrypted)
    if not verify_totp_code(secret, req.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code.",
        )

    # 启用
    current_user.totp_enabled = True
    current_user.totp_enabled_at = datetime.now(timezone.utc)

    # 一次性生成 10 个 recovery codes,server 只存 sha256 hash
    codes_plain = generate_recovery_codes()
    # 清掉历史(理论上不该有,稳妥起见)
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == current_user.id))
    for code in codes_plain:
        db.add(RecoveryCode(user_id=current_user.id, code_hash=hash_recovery_code(code)))

    db.commit()
    logger.info("2fa.confirm.enabled user=%s", current_user.id)
    return TwoFAConfirmResponse(enabled=True, recovery_codes=codes_plain)


# ---------------- Verify (login 第二步,challenge_token + code → 真 token) ----------------


@router.post("/verify", response_model=AuthLoginResponse)
def verify(
    req: TwoFAVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AuthLoginResponse:
    # 防暴力破解:同一 IP + challenge 5 次/分钟
    _check_verify_rate_limit(request, req.challenge_token)

    try:
        payload = decode_2fa_challenge_token(req.challenge_token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired challenge token.",
        ) from exc

    user_id = payload.get("sub")
    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.is_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User disabled")
    if not user.totp_enabled or not user.totp_secret_encrypted:
        # 防御:用户在 challenge 期间禁用了 2FA → 让前端重新 login
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA not enabled for this account.",
        )

    # 校验 code
    code_ok = False
    if req.method == "totp":
        secret = decrypt_totp_secret(user.totp_secret_encrypted)
        code_ok = verify_totp_code(secret, req.code)
    elif req.method == "recovery_code":
        target_hash = hash_recovery_code(req.code)
        # 拉所有未使用的 code,逐个常量时间比对(防 timing attack)
        rc_list = db.scalars(
            select(RecoveryCode).where(
                RecoveryCode.user_id == user.id,
                RecoveryCode.used_at.is_(None),
            )
        ).all()
        matched = None
        for candidate in rc_list:
            if constant_time_match(candidate.code_hash, target_hash):
                matched = candidate
                break
        if matched is not None:
            matched.used_at = datetime.now(timezone.utc)
            code_ok = True

    if not code_ok:
        logger.warning(
            "2fa.verify.failed user=%s method=%s ip=%s",
            user.id, req.method,
            request.client.host if request.client else "?",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid 2FA code.",
        )

    # ✅ 验证通过 → 走跟普通 login 一样的发 token 流程
    # 复用 auth.py 内的 _upsert_device + _issue_tokens(私下导入,可接受)
    from .auth import _issue_tokens, _upsert_device

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
        "2fa.verify.success user=%s method=%s device=%s",
        user.id, req.method, device.id,
    )
    return AuthLoginResponse(
        requires_2fa=False,
        user=token_response.user,
        access_token=token_response.access_token,
        refresh_token=token_response.refresh_token,
        expires_in=token_response.expires_in,
        device_id=token_response.device_id,
        scopes=token_response.scopes,
    )


# ---------------- Disable ----------------


@router.post("/disable")
def disable(
    req: TwoFADisableRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA not enabled.",
        )

    if not verify_password(req.password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password.",
        )
    if not current_user.totp_secret_encrypted or not verify_totp_code(
        decrypt_totp_secret(current_user.totp_secret_encrypted), req.code
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code.",
        )

    current_user.totp_secret_encrypted = None
    current_user.totp_enabled = False
    current_user.totp_enabled_at = None
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == current_user.id))
    db.commit()
    logger.info("2fa.disable user=%s", current_user.id)
    return {"disabled": True}


# ---------------- Regenerate recovery codes ----------------


@router.post("/recovery-codes/regenerate", response_model=TwoFARegenerateResponse)
def regenerate(
    req: TwoFARegenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TwoFARegenerateResponse:
    if not current_user.totp_enabled or not current_user.totp_secret_encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA not enabled.",
        )

    secret = decrypt_totp_secret(current_user.totp_secret_encrypted)
    if not verify_totp_code(secret, req.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code.",
        )

    # 旧 codes 全删
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == current_user.id))
    codes_plain = generate_recovery_codes()
    for code in codes_plain:
        db.add(RecoveryCode(user_id=current_user.id, code_hash=hash_recovery_code(code)))
    db.commit()
    logger.info("2fa.regenerate user=%s count=%d", current_user.id, len(codes_plain))
    return TwoFARegenerateResponse(recovery_codes=codes_plain)
