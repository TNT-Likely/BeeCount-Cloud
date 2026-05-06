from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "BeeCount Cloud"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    web_static_dir: str = "/app/static"

    database_url: str = Field(default="sqlite:///./beecount.db")

    jwt_secret: str = Field(default="change-me-in-production-at-least-32-bytes")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    cors_origins: str = "http://localhost:8080,http://localhost:5173,http://localhost:3000"
    rate_limit_window_seconds: int = 60
    rate_limit_max_requests: int = 30
    backup_storage_dir: str = "./data/backups"
    backup_max_upload_bytes: int = 64 * 1024 * 1024
    attachment_storage_dir: str = "./data/attachments"
    attachment_max_upload_bytes: int = 64 * 1024 * 1024

    # ===== rclone 备份模块 =====
    # rclone.conf 路径(权限 0600,只 server 进程读写)。默认放在 DATA_DIR
    # 同级,跟 backup volume 一起被备份覆盖外部脚本时也能保留。
    rclone_config_path: str = "./data/rclone.conf"
    # rclone 二进制路径,Docker 镜像里 apt 装的会在 /usr/bin/rclone。
    rclone_binary: str = "rclone"
    # 备份打包 + 还原解压的临时区。需要 ≥ 2x DATA_DIR 大小。
    backup_staging_dir: str = "./data/backup-staging"
    # 还原(restore)隔离目录 —— 服务端只往这写,绝不动 live data。
    restore_dir: str = "./data/restore"
    # 调度器开关。测试和某些命令行场景关掉避免后台 thread 干扰。
    backup_scheduler_enabled: bool = Field(default=True, alias="BACKUP_SCHEDULER_ENABLED")
    # 调度器时区(影响 cron 解释)。空 = 走 tzlocal(读 TZ env 或 /etc/localtime)。
    # 显式设置 IANA 时区名(如 'Asia/Shanghai')可绕开 tzlocal 失效坑 — 容器
    # 没装 tzdata 时 tzlocal 会静默 fallback UTC,"0 4 * * *" 就在 UTC 4 点
    # 跑(不是用户期望的本地 4 点)。
    scheduler_timezone: str = Field(default="", alias="SCHEDULER_TIMEZONE")
    device_online_window_minutes: int = 10
    allow_app_rw_scopes: bool = True

    # Open registration is a footgun on self-hosted deployments: anyone with
    # the public URL could create a user. Default OFF; operators set this to
    # true during bootstrap, create the first admin, then flip back to false.
    # Admins can still create users via POST /api/v1/admin/users regardless.
    registration_enabled: bool = Field(default=False, alias="REGISTRATION_ENABLED")

    # Legacy strict `base_change_id` check on /write/* endpoints. When mobile
    # fullPush is streaming changes, the server-side materializer bumps the
    # latest ledger_snapshot change_id faster than any web retry can catch up,
    # producing endless 409s. With this flag OFF (default) we drop the strict
    # equality check and fall back to per-entity LWW for actual conflict
    # resolution. Set to ``true`` to re-enable the old behavior if something
    # regresses in the field.
    strict_base_change_id: bool = Field(default=False, alias="STRICT_BASE_CHANGE_ID")

    # ===== 2FA(TOTP)=====
    # authenticator app 扫描 QR 后展示的"账号名"前缀。默认 "BeeCount",
    # 自托管用户可以改成自己的品牌(如 "蜜蜂记账云" / "MyAcme")。
    totp_issuer_name: str = Field(default="BeeCount", alias="TOTP_ISSUER_NAME")
    # otpauth URI 上挂的 image= 参数,部分 authenticator app(Microsoft Authenticator
    # 等)会取这个 URL 显示账号 logo。需要是公网可访问的 https PNG/SVG。
    # 空字符串 = 不附加 image 参数。Google Authenticator 不支持这个参数。
    # 推荐值:'https://<your-host>/branding/logo.png'
    totp_image_url: str = Field(default="", alias="TOTP_IMAGE_URL")

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

    @property
    def is_default_jwt_secret(self) -> bool:
        return self.jwt_secret in {
            "change-me",
            "change-me-in-production",
            "change-me-in-production-at-least-32-bytes",
        }

    @property
    def is_weak_jwt_secret(self) -> bool:
        return len(self.jwt_secret.encode("utf-8")) < 32

    @property
    def has_wildcard_cors(self) -> bool:
        return any(origin == "*" for origin in self.cors_origin_list)


@lru_cache
def get_settings() -> Settings:
    return Settings()
