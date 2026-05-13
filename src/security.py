import hashlib
import hmac
import secrets
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
# MCP 专用 scope。PAT 创建时用户选这两个之一或两个都选。MCP server 的
# read tools 要求 `mcp:read`,write tools 要求 `mcp:write`。详见 .docs/mcp-
# server-design.md。
SCOPE_MCP_READ = "mcp:read"
SCOPE_MCP_WRITE = "mcp:write"

# PAT token 明文前缀,识别"这是 BeeCount MCP token"。跟 GitHub PAT `ghp_` /
# OpenAI `sk-` 同惯例,方便用户和 secret scanner 识别。
PAT_PREFIX = "bcmcp_"
# Token 主体随机字节数。32 字节 base64url 后约 43 字符,加 PAT_PREFIX 共 ~49,
# 熵 256 bit 远超暴力破解。
PAT_RANDOM_BYTES = 32
# 列表展示用前缀长度(明文,含 PAT_PREFIX),如 `bcmcp_a1b2c3d4`,共 14 字符。
# 既能识别 PAT 类型,又能区分用户的多个 token,但还不够撞库。
PAT_DISPLAY_PREFIX_LEN = 14


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


def create_2fa_challenge_token(
    user_id: str,
    *,
    expires_in_seconds: int = 300,
    client_type: str = "app",
) -> str:
    """5 分钟短期 JWT,type=totp_challenge。用户输完 6 位码后回 /2fa/verify 兑换真 token。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "type": "totp_challenge",
        "client_type": client_type,
        "jti": uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_2fa_challenge_token(token: str) -> dict:
    """解码并校验 type=totp_challenge。过期 / 签名错 / 类型错都抛异常。"""
    payload = decode_token(token)
    if payload.get("type") != "totp_challenge":
        raise ValueError("Invalid token type for 2FA challenge")
    return payload


# --------------------------------------------------------------------------- #
# PAT (Personal Access Token) — 长期 token 给 MCP / 外部 LLM 客户端用。
# 详见 .docs/mcp-server-design.md。
# --------------------------------------------------------------------------- #


def generate_pat() -> tuple[str, str, str]:
    """生成新 PAT。

    返回 `(plaintext, token_hash, display_prefix)`:
      - plaintext:`bcmcp_<base64url 32 字节>`,**仅返回一次**给用户,不存表
      - token_hash:sha256 hex,存表用
      - display_prefix:前 14 字符(如 `bcmcp_a1b2c3d4`),列表展示用

    不用 PBKDF2 / bcrypt 是因为校验路径需要常数时间且很快(MCP 每次 tool
    call 都跑一次),sha256 + timing-safe compare 足够安全 — token 本身已是
    256 bit 熵的随机串,不像密码需要抗暴力破解。
    """
    raw = secrets.token_urlsafe(PAT_RANDOM_BYTES)
    plaintext = f"{PAT_PREFIX}{raw}"
    token_hash = hash_token(plaintext)
    display_prefix = plaintext[:PAT_DISPLAY_PREFIX_LEN]
    return plaintext, token_hash, display_prefix


def looks_like_pat(token: str) -> bool:
    """快速判断一个 token 字符串看着像不像 PAT。

    用在 Auth header 路由分流上 — `Authorization: Bearer <token>` 同时接受
    JWT access token 和 PAT,看前缀决定走哪条校验路径,避免给每个请求都做
    两遍解码。
    """
    return token.startswith(PAT_PREFIX)


def verify_pat_hash(provided_token: str, stored_hash: str) -> bool:
    """常数时间比较 PAT hash,防 timing attack 推测正确 hash。"""
    return hmac.compare_digest(hash_token(provided_token), stored_hash)
