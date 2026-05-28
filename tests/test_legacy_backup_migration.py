"""历史备份缺少运行时表时的 Alembic 兼容性迁移测试。"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from src import models  # noqa: F401
from src.config import get_settings
from src.database import Base
from src.services.backup.db_snapshot import DEFAULT_EXCLUDED_TABLES

_LEGACY_HEAD = "0013_user_category_parent_sync_id"


def _upgrade_to_head(db_url: str, monkeypatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("DATABASE_URL", db_url)
    get_settings.cache_clear()
    try:
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "alembic"))
        command.upgrade(cfg, "head")
    finally:
        get_settings.cache_clear()


def test_repair_migration_recreates_runtime_tables_missing_from_legacy_backup(
    tmp_path,
    monkeypatch,
) -> None:
    """旧备份曾误 DROP 运维表,但 alembic_version 已在 head;升级应补回空表。"""
    db_path = tmp_path / "legacy-backup.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url)
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        # 模拟旧版云备份:DEFAULT_EXCLUDED_TABLES 里的表被 DROP,用户数据表仍在。
        for table_name in (
            "mcp_call_logs",
            "backup_run_targets",
            "backup_runs",
            "audit_logs",
            "sync_push_idempotency",
            "refresh_tokens",
        ):
            conn.execute(text(f"DROP TABLE {table_name}"))
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:version)"),
            {"version": _LEGACY_HEAD},
        )
    engine.dispose()

    _upgrade_to_head(db_url, monkeypatch)

    upgraded_engine = create_engine(db_url)
    try:
        existing_tables = set(inspect(upgraded_engine).get_table_names())
        for table_name in DEFAULT_EXCLUDED_TABLES:
            assert table_name in existing_tables

        # 登录路径依赖 refresh_tokens 可写;这里直接插入验证补建表结构可用。
        with upgraded_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, is_admin, "
                    "is_enabled, totp_enabled, created_at) "
                    "VALUES ('u1', 'u1@example.com', 'h', 1, 1, 0, "
                    "CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO refresh_tokens "
                    "(id, user_id, device_id, token_hash, expires_at, created_at) "
                    "VALUES ('rt1', 'u1', NULL, 'hash1', CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                )
            )
    finally:
        upgraded_engine.dispose()
