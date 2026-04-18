"""一次性启动引导 —— 把"必须手填"的配置降级成"开箱即用"。

目前只处理 `JWT_SECRET`:
- 用户自己设了强密钥 → 直接用,不干扰
- 用户没设 / 用了占位符 → 读 `/data/.jwt_secret`,没有就随机生成 64 hex 并落盘

这样 docker compose 最小配置只需要一个 volume,不用用户掏 `openssl rand` 再
往 compose 里贴。单用户自部署场景下,密钥跟 DB 在同一个 volume,备份 /
迁移一起走,足够安全。
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

_JWT_SECRET_ENV = "JWT_SECRET"
_SECRET_FILE_NAME = ".jwt_secret"
_PLACEHOLDER_SECRETS = {
    "",
    "change-me",
    "change-me-in-production",
    "change-me-in-production-at-least-32-bytes",
}
_MIN_SECRET_BYTES = 32


def _is_strong_secret(value: str) -> bool:
    if value in _PLACEHOLDER_SECRETS:
        return False
    return len(value.encode("utf-8")) >= _MIN_SECRET_BYTES


def ensure_jwt_secret(data_dir: str | None = None) -> None:
    """Populate `JWT_SECRET` env var from persisted file, or generate one.

    - 用户显式提供强密钥:保留
    - data volume 已有 `.jwt_secret`:读出来写进 env
    - 都没有:随机生成 64 hex (32 bytes),落到 `<data_dir>/.jwt_secret` chmod 600
    """
    current = os.environ.get(_JWT_SECRET_ENV, "")
    if _is_strong_secret(current):
        return

    root = Path(data_dir or os.environ.get("DATA_DIR") or "/data")
    secret_path = root / _SECRET_FILE_NAME

    if secret_path.exists() and secret_path.is_file():
        try:
            loaded = secret_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("bootstrap: failed to read %s: %s", secret_path, exc)
            loaded = ""
        if _is_strong_secret(loaded):
            os.environ[_JWT_SECRET_ENV] = loaded
            logger.info(
                "bootstrap: JWT_SECRET loaded from %s (len=%d)",
                secret_path,
                len(loaded),
            )
            return

    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error(
            "bootstrap: cannot create data dir %s for JWT secret: %s; "
            "JWT_SECRET must be provided via environment",
            root,
            exc,
        )
        return

    new_secret = secrets.token_hex(_MIN_SECRET_BYTES)  # 64 hex chars = 32 bytes
    try:
        secret_path.write_text(new_secret, encoding="utf-8")
        try:
            secret_path.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        logger.error(
            "bootstrap: cannot write %s: %s; JWT_SECRET must be provided via environment",
            secret_path,
            exc,
        )
        return

    os.environ[_JWT_SECRET_ENV] = new_secret
    logger.warning(
        "bootstrap: JWT_SECRET was missing/placeholder; generated a new 32-byte "
        "secret at %s. Back up this file along with your database.",
        secret_path,
    )
