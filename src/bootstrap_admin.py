"""Auto-create the initial admin on first boot so web login works out-of-box.

Docker 部署场景里没有 Makefile,用户 `docker compose up -d` 起完以为就能登录
—— 但数据库是空的,没有任何 user 行。这个 bootstrap 专门修这个缺口:

策略(按优先级):
1. **已存在任何 user** → 跳过,什么都不做(幂等)
2. **env 里有 `BOOTSTRAP_ADMIN_EMAIL` + `BOOTSTRAP_ADMIN_PASSWORD`** → 按这对凭证创建 admin
3. **否则** → 用 `owner@example.com` + **随机生成的 16 字符密码**创建 admin
   - 密码同时落到 `<DATA_DIR>/.initial_admin_password`(600 权限)给运维兜底
   - 日志里打印醒目横幅,`docker compose logs` 肉眼一眼看到

为什么不用固定默认密码:自部署容器常暴露公网,"owner@example.com / 123456"
是众所周知的蜜罐。随机 16 字符 + 持久化到 volume 里,安全 + 可恢复。
"""

from __future__ import annotations

import logging
import os
import secrets
import string
from pathlib import Path

from sqlalchemy import select

from .database import SessionLocal
from .models import User
from .security import hash_password

logger = logging.getLogger(__name__)

_DEFAULT_EMAIL = "owner@example.com"
_PASSWORD_FILE_NAME = ".initial_admin_password"
_PASSWORD_ALPHABET = string.ascii_letters + string.digits  # 16^62 ≈ 2^95,足够强


def _generate_password() -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(16))


def _persist_password(password: str, email: str) -> Path | None:
    """把密码落到 DATA_DIR/.initial_admin_password,方便运维后续捞。"""
    data_dir = os.environ.get("DATA_DIR") or "/data"
    target = Path(data_dir) / _PASSWORD_FILE_NAME
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        content = f"email: {email}\npassword: {password}\n"
        target.write_text(content, encoding="utf-8")
        try:
            target.chmod(0o600)
        except OSError:
            pass
        return target
    except OSError as exc:
        logger.warning("bootstrap_admin: 无法写 %s: %s", target, exc)
        return None


def _log_credentials(email: str, password: str, persisted_at: Path | None) -> None:
    """打出醒目横幅,uvicorn stdout 里 docker compose logs 一眼看到。"""
    banner = "=" * 72
    lines = [
        "",
        banner,
        " BeeCount Cloud — 初次启动,已自动创建管理员账号:",
        "",
        f"   邮箱:    {email}",
        f"   密码:    {password}",
        "",
    ]
    if persisted_at is not None:
        lines.append(f"   凭证已落盘到 {persisted_at}(volume 内,600 权限)")
    lines.extend([
        "",
        " ⚠️  登录后请立即修改密码,或下次启动前设置环境变量:",
        "     BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD",
        banner,
        "",
    ])
    for line in lines:
        logger.warning(line)


def ensure_admin() -> None:
    """如果库里一个 user 都没有,建一个 admin 让 web 登录可用。幂等。"""
    db = SessionLocal()
    try:
        any_user = db.scalar(select(User).limit(1))
        if any_user is not None:
            return  # 已经有用户,不插手

        env_email = (os.environ.get("BOOTSTRAP_ADMIN_EMAIL") or "").strip().lower()
        env_password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD") or ""

        if env_email and env_password:
            email, password = env_email, env_password
            source = "env BOOTSTRAP_ADMIN_EMAIL/PASSWORD"
            persisted_at: Path | None = None
        else:
            email = _DEFAULT_EMAIL
            password = _generate_password()
            source = "auto-generated"
            persisted_at = _persist_password(password, email)

        admin = User(
            email=email,
            password_hash=hash_password(password),
            is_admin=True,
            is_enabled=True,
        )
        db.add(admin)
        db.commit()
        logger.info(
            "bootstrap_admin: created initial admin email=%s source=%s",
            email,
            source,
        )
        if source == "auto-generated":
            _log_credentials(email, password, persisted_at)
    except Exception:
        logger.exception("bootstrap_admin: failed to create initial admin")
    finally:
        db.close()
