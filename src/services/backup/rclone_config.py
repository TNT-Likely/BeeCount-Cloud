"""rclone.conf 管理 —— 把 BackupRemote 表里的配置渲染成 rclone 能读的 ini
格式,放在 `<DATA_DIR>/rclone.conf` (0600)。

rclone 自己也有 `rclone config touch` API,但走 subprocess 写入对原子性 + 错
误处理更难,我们直接生成文件:
  - DB 里存非敏感字段(name / backend_type / config_summary 的部分)
  - 敏感字段(access keys 等)以**明文**存 DB 的 `_secrets`,写入 conf 时也明文
    (rclone v1.73.5 s3 backend 不会自动 reveal obscured secrets,obscured 字符
    串会被原样当作 secret,签名直接错)
  - 文件每次"全量重写"(不增量 patch),保证状态唯一权威 = DB

加密方式:tarball 加密走 age(详见 age_runner.py),不再用 rclone crypt。
rclone.conf 现在永远是单段 backend(s3 / r2 / webdav / ...),不挂 crypt 装饰。
"""
from __future__ import annotations

import logging
import os
import subprocess
from configparser import ConfigParser
from io import StringIO
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ...models import BackupRemote


logger = logging.getLogger(__name__)


def obscure_password(plain: str, *, rclone_binary: str = "rclone") -> str:
    """跑 `rclone obscure <plain>` —— 现在用不到了(crypt 段已移除),保留作
    `reveal_password` 的对偶用于老数据迁移检测。"""
    if not plain:
        return ""
    result = subprocess.run(
        [rclone_binary, "obscure", plain],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return result.stdout.strip()


def _mask_conf(content: str) -> str:
    """rclone.conf 内容脱敏后给日志 — 把 sensitive 字段值替换成 ***。"""
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("["):
            key, _, val = stripped.partition("=")
            key_clean = key.strip()
            val_clean = val.strip()
            if key_clean in SENSITIVE_FIELDS:
                lines.append(f"{key_clean} = ***({len(val_clean)} chars)")
                continue
        lines.append(line)
    return "\n".join(lines)


def reveal_password(obscured: str, *, rclone_binary: str = "rclone") -> str:
    """`rclone reveal` 把 obscured 还原成明文。obscure 不是真加密,这是
    可逆的混淆 — rclone CLI 自己也用同一个 key。

    用途:老数据迁移 — 历史版本对 s3 secret 也做了 obscure,现在改为明文存,
    rewrite_from_db 检测到 obscured 形式就 reveal 后回写 DB。

    输入不是合法 obscured 字符串(已经是明文 / 损坏)时:rclone reveal
    可能 returncode != 0,或 stdout 出来不是 UTF-8 字节。两种情况都返回
    空字符串(=「不是 obscured,保持原值」语义),不让上层 500。
    """
    if not obscured:
        return ""
    try:
        result = subprocess.run(
            [rclone_binary, "reveal", obscured],
            capture_output=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            return ""
        try:
            return result.stdout.decode("utf-8").strip()
        except UnicodeDecodeError:
            return ""
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return ""


# config 字段不是任意的 — 我们维护一个 backend_type → 允许字段的白名单,
# UI 表单和 server 校验都用这个。第一版只支持高频后端,需要更多再加。
ALLOWED_BACKEND_FIELDS: dict[str, set[str]] = {
    "s3": {
        "provider",
        "access_key_id",
        "secret_access_key",
        "region",
        "endpoint",
        "bucket",  # 实际不写入 conf,而是用作 path prefix(`base:bucket`)
    },
    "b2": {"account", "key", "bucket"},
    "drive": {"client_id", "client_secret", "scope", "token", "root_folder_id"},
    "onedrive": {"client_id", "client_secret", "token", "drive_id", "drive_type"},
    "dropbox": {"client_id", "client_secret", "token"},
    "webdav": {"url", "vendor", "user", "pass"},
    "sftp": {"host", "user", "pass", "port", "key_file"},
    "ftp": {"host", "user", "pass", "port"},
    "alias": {"remote"},
    "local": set(),  # 没字段,直接 type=local
}

# 这些字段被视作"密语类":存 DB 时作为 _secrets 字段,UI / config_summary
# 不展示。其它字段(region / endpoint / bucket 等)明文展示。
SENSITIVE_FIELDS = {
    "secret_access_key",
    "key",
    "client_secret",
    "token",
    "pass",
    "password",
}


def sanitize_config_summary(backend_type: str, config: dict[str, Any]) -> dict[str, Any]:
    """从 user 提交的 config dict 里挑出非敏感字段(给 UI 展示用)。"""
    allowed = ALLOWED_BACKEND_FIELDS.get(backend_type, set())
    out: dict[str, Any] = {}
    for k, v in config.items():
        if k not in allowed:
            continue
        if k in SENSITIVE_FIELDS:
            out[k] = "******"
        else:
            out[k] = v
    return out


# 这些字段不写入 rclone.conf,只作为 path prefix(每次 push/list/delete 时
# 拼到 `<remote>:<bucket>/<filename>`)。bucket 是 S3/B2 等对象存储的概念,
# rclone s3 backend 不在 conf 里存 bucket — 它是路径的一部分。
PATH_PREFIX_FIELDS = {"bucket"}


def _conf_field_keys(backend_type: str) -> set[str]:
    """这个 backend 真正写到 rclone.conf 的 key 集合(allowed - path-prefix)。"""
    return ALLOWED_BACKEND_FIELDS.get(backend_type, set()) - PATH_PREFIX_FIELDS


def remote_path(remote, filename: str) -> str:
    """给定 BackupRemote 和文件名,返回 rclone 命令行用的完整 path。

    形如 `<name>:<bucket>/<filename>`,bucket 没设置则 `<name>:<filename>`。
    rclone.conf 里永远是单段 backend,encrypted 标志不影响 path 格式 —
    加密由 age 在 backup runner 里完成,rclone 看到的就是普通 .tar.gz.age 文件。
    """
    cfg = remote.config_summary or {}
    bucket = cfg.get("bucket") if isinstance(cfg, dict) else None
    if isinstance(bucket, str) and bucket.strip():
        return f"{remote.name}:{bucket.strip()}/{filename}"
    return f"{remote.name}:{filename}"


def remote_root(remote) -> str:
    """`<remote>:` 或 `<remote>:<bucket>/` —— 用于 lsjson / 路径 root 操作。"""
    cfg = remote.config_summary or {}
    bucket = cfg.get("bucket") if isinstance(cfg, dict) else None
    if isinstance(bucket, str) and bucket.strip():
        return f"{remote.name}:{bucket.strip()}/"
    return f"{remote.name}:"


# ============================================================================
# Age passphrase 在 _secrets 里的 key 名(不是 backend 字段,不写入 rclone.conf)
# ============================================================================

AGE_PASSPHRASE_KEY = "age_passphrase"


def get_age_passphrase(remote: BackupRemote) -> str | None:
    """从 BackupRemote.config_summary._secrets 拿 age passphrase。"""
    cfg = remote.config_summary or {}
    secrets = cfg.get("_secrets") if isinstance(cfg, dict) else None
    if not isinstance(secrets, dict):
        return None
    val = secrets.get(AGE_PASSPHRASE_KEY)
    if isinstance(val, str) and val:
        return val
    return None


class RcloneConfigManager:
    """单 user-scope 的 rclone.conf 管理。

    用法:
        mgr = RcloneConfigManager(conf_path, rclone_binary)
        mgr.rewrite_from_db(db, user_id)        # 全量重写
        mgr.read_text()                         # 读当前 conf 文本(给 download 端点)
    """

    def __init__(self, conf_path: str | Path, rclone_binary: str = "rclone"):
        self.conf_path = Path(conf_path)
        self.rclone_binary = rclone_binary

    def ensure_dir(self) -> None:
        self.conf_path.parent.mkdir(parents=True, exist_ok=True)

    def read_text(self) -> str:
        if not self.conf_path.exists():
            return ""
        return self.conf_path.read_text(encoding="utf-8")

    # ----------------------------------------------------------------- write
    def rewrite_from_db(self, db: Session, user_id: str, *, repair_secrets: bool = True) -> None:
        """读 DB 里所有 BackupRemote,生成完整 rclone.conf,原子写入。

        DB 的 `_secrets` 里**统一存明文**。rewrite 时:
          - backend secrets(s3 secret_access_key / b2 key / drive token 等)
            明文写入 conf
          - age_passphrase **不写入 conf**(它不是 rclone 字段,只用于
            backup runner 调 age 加密 tarball)

        repair_secrets=True 时(默认):兼容老数据 — 如果 `_secrets` 里
        是历史的 obscured 形式(老版本对 s3 secret 也 obscure 过),reveal
        后回写 DB 为明文。
        """
        remotes = (
            db.query(BackupRemote)
            .filter(BackupRemote.user_id == user_id)
            .order_by(BackupRemote.id.asc())
            .all()
        )
        parser = ConfigParser()
        for r in remotes:
            cfg = dict(r.config_summary or {})
            secrets = cfg.pop("_secrets", {}) if isinstance(cfg.get("_secrets"), dict) else {}

            # secrets 老数据迁移到明文
            cleaned_secrets: dict[str, str] = {}
            plain_secrets: dict[str, str] = {}
            for k, v in (secrets or {}).items():
                stored = str(v)
                if repair_secrets and stored:
                    revealed = reveal_password(
                        stored, rclone_binary=self.rclone_binary
                    )
                    if revealed and revealed != stored:
                        cleaned_secrets[k] = revealed
                        stored = revealed
                        logger.info(
                            "remote %s field %s migrated obscured->plain in DB",
                            r.name, k,
                        )
                    else:
                        clean = stored.strip()
                        if clean != stored:
                            cleaned_secrets[k] = clean
                            stored = clean
                plain_secrets[k] = stored

            # 单段 backend(s3 / r2 / webdav / ...)。crypt 段已移除。
            section = r.name
            parser[section] = {"type": r.backend_type}
            for k, v in cfg.items():
                if k.startswith("_"):
                    continue
                if k in SENSITIVE_FIELDS:
                    continue
                if k in PATH_PREFIX_FIELDS:
                    continue
                clean = str(v).strip() if isinstance(v, str) else v
                parser[section][k] = str(clean)
            for k, v in plain_secrets.items():
                # age_passphrase 不写入 conf,它由 backup runner 直接读
                if k == AGE_PASSPHRASE_KEY:
                    continue
                # 老 DB 可能留着 password / password2 历史字段,跳过
                if k in {"password", "password2"}:
                    continue
                parser[section][k] = v

            # 回写迁移过的 secrets
            if cleaned_secrets:
                new_summary = dict(r.config_summary or {})
                old_secrets = dict(new_summary.get("_secrets") or {})
                old_secrets.update(cleaned_secrets)
                new_summary["_secrets"] = old_secrets
                r.config_summary = new_summary
                db.flush()

        out = StringIO()
        parser.write(out)

        # 原子 rewrite
        self.ensure_dir()
        tmp = self.conf_path.with_suffix(self.conf_path.suffix + ".tmp")
        tmp.write_text(out.getvalue(), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.conf_path)
        logger.info("rclone.conf rewritten: %d remotes", len(remotes))

    # ----------------------------------------------------------------- test
    def test_path(self, path: str) -> tuple[bool, str | None, list[str] | None]:
        """跑 `rclone lsd <path>` 验证连通性。返回 (ok, error, listing)。"""
        cmd = [
            self.rclone_binary,
            "--config",
            str(self.conf_path),
            "lsd",
            path,
            "--max-depth",
            "1",
            "-vv",
        ]
        logger.info("rclone test: cmd=%s", " ".join(cmd))
        try:
            conf_content = self.conf_path.read_text(encoding="utf-8") if self.conf_path.exists() else ""
            logger.info(
                "rclone test: conf summary:\n%s",
                _mask_conf(conf_content),
            )
        except OSError as exc:
            logger.warning("rclone test: failed to read conf: %s", exc)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return False, "rclone timed out (30s)", None
        except FileNotFoundError:
            return False, f"rclone binary not found: {self.rclone_binary}", None

        if result.stderr:
            logger.info("rclone test stderr (rc=%d):\n%s", result.returncode, result.stderr)
        if result.stdout:
            logger.info("rclone test stdout:\n%s", result.stdout)

        if result.returncode != 0:
            err = (result.stderr or "rclone failed").strip()
            return False, err[-1500:], None
        listing = [
            line.strip().split(maxsplit=4)[-1] if line.strip() else ""
            for line in result.stdout.splitlines()
            if line.strip()
        ]
        return True, None, listing[:50]

    # 兼容旧调用(测试用),内部走 test_path
    def test_remote(self, name: str) -> tuple[bool, str | None, list[str] | None]:
        return self.test_path(f"{name}:")
