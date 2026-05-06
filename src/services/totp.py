"""2FA TOTP service.

设计文档:.docs/2fa-design.md(第四章)。

核心职责:
1. TOTP secret 加解密(Fernet,key 从 JWT_SECRET 派生)
2. 6 位验证码校验(±30 秒窗口,RFC 6238)
3. Recovery code 生成 / 校验 / 一次性消费
4. otpauth:// URI 拼接(供 Web 端生成 QR)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from urllib.parse import quote

import pyotp
from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings


_RECOVERY_CODE_COUNT = 10
_RECOVERY_CODE_BYTES = 5  # 5 字节 → 8 字符 base32 → 格式化成 4-4 形式


def _derive_fernet_key() -> bytes:
    """从 JWT_SECRET sha256 → urlsafe_b64 → 32 字节 Fernet key。

    复用 JWT_SECRET 是为了少一个机密管理负担(JWT_SECRET 已经是部署级机密,
    丢了 = JWT 全失效,所以 totp_secret 跟它绑同一安全域是合理的)。
    JWT_SECRET 轮换时 totp_secret_encrypted 全失效 — 文档已提示运维注意。
    """
    settings = get_settings()
    raw = hashlib.sha256(settings.jwt_secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(raw)


_fernet_singleton: Fernet | None = None


def _fernet() -> Fernet:
    """懒加载,首次调用时再读 settings — 避免 import 期 settings 未初始化。"""
    global _fernet_singleton
    if _fernet_singleton is None:
        _fernet_singleton = Fernet(_derive_fernet_key())
    return _fernet_singleton


def encrypt_totp_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_totp_secret(encrypted: str) -> str:
    try:
        return _fernet().decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("totp_secret decrypt failed (JWT_SECRET 轮换?)") from e


def generate_totp_secret() -> str:
    """生成新的 base32 secret(20 字节熵 → 32 字符)。"""
    return pyotp.random_base32()


def build_otpauth_uri(secret: str, account_label: str) -> str:
    """拼 otpauth://totp/<issuer>:<email>?secret=...&issuer=<issuer>[&image=<url>]

    - issuer 来自 settings.totp_issuer_name(默认 "BeeCount",自托管用户可改)
    - image 来自 settings.totp_image_url(可选;Microsoft Authenticator 等支持)

    account_label 通常是用户邮箱;authenticator app 显示为 "<issuer>: foo@bar"。
    """
    settings = get_settings()
    issuer_name = settings.totp_issuer_name or "BeeCount"
    issuer = quote(issuer_name, safe="")
    label = quote(f"{issuer_name}:{account_label}", safe="")
    params = [
        f"secret={secret}",
        f"issuer={issuer}",
        "algorithm=SHA1",
        "digits=6",
        "period=30",
    ]
    image_url = (settings.totp_image_url or "").strip()
    if image_url:
        params.append(f"image={quote(image_url, safe=':/?&=._-')}")
    return f"otpauth://totp/{label}?{'&'.join(params)}"


def verify_totp_code(secret: str, code: str) -> bool:
    """6 位 TOTP 校验,容忍 ±30 秒时钟偏移(valid_window=1)。

    code 输入应该已经 strip / 去空格,这里再保险一层。
    """
    if not code or not secret:
        return False
    cleaned = code.strip().replace(" ", "")
    if not cleaned.isdigit() or len(cleaned) != 6:
        return False
    return pyotp.TOTP(secret).verify(cleaned, valid_window=1)


# ---------------- Recovery codes ----------------


def generate_recovery_codes(count: int = _RECOVERY_CODE_COUNT) -> list[str]:
    """生成 N 个一次性恢复码,格式 `xxxx-xxxx`(base32 小写)。

    每个 code 5 字节熵 → base32 8 字符,人眼可读,authenticator 兼容字符集。
    """
    codes: list[str] = []
    for _ in range(count):
        raw = secrets.token_bytes(_RECOVERY_CODE_BYTES)
        b32 = base64.b32encode(raw).decode("ascii").lower().rstrip("=")
        # 8 字符切两半,用 `-` 分隔
        codes.append(f"{b32[:4]}-{b32[4:]}")
    return codes


def hash_recovery_code(code: str) -> str:
    """sha256 hex,跟 password_hash 一样不可逆。

    输入 normalize:去空格、转小写、剥 `-` — 用户手输时大小写 / 分隔符随意。
    """
    normalized = _normalize_recovery_code(code)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_recovery_code(code: str) -> str:
    return code.strip().lower().replace("-", "").replace(" ", "")


def constant_time_match(a: str, b: str) -> bool:
    """常量时间比较两个 hash,防 timing attack。"""
    return hmac.compare_digest(a, b)
