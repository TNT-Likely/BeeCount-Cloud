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
