"""SQLite 快照 —— 用 `VACUUM INTO` 拿一致的单文件输出。

WAL 模式下直接 `cp beecount.db` 不安全:
  - WAL 段还没 checkpoint,目标文件少事务
  - 备份过程中读到的内容半截

`VACUUM INTO 'path'` 是原子的 + 已 checkpoint + 输出永远是单文件,无 -shm /
-wal 噪音。需要短暂 read 锁(~ms~s 级),不阻塞写。

PostgreSQL 后续再加(用 `pg_dump --format=custom`),目前只 SQLite。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)


# 备份默认排除的"运维类"表 — 不属于用户数据,留着只让 tar 变大 + 暴露
# 内部细节:
#   - backup_runs / backup_run_targets:备份运行历史(restore 后没意义)
#   - sync_push_idempotency:24 小时滚动 idempotency 缓存
#   - audit_logs:管理员操作日志,运维痕迹,不属于"账本数据"
#   - refresh_tokens:登录 session,restore 后所有人都得重登,留着没用
#   - mcp_call_logs:MCP tool 调用审计,30 天滚动遥测,跟账本数据无关
# **PAT 表 (personal_access_tokens) 要保留** — 用户的 LLM 客户端配置依赖
# 这些 token,restore 后 LLM 仍然能连上,不用重新发 token。
# 用户数据相关(必须保留):users / user_profiles / devices / ledgers /
# sync_changes / sync_cursors / read_*_projection / attachment_files /
# personal_access_tokens / backup_remotes / backup_schedules /
# backup_schedule_remotes(配置要保留)
DEFAULT_EXCLUDED_TABLES = (
    "backup_runs",
    "backup_run_targets",
    "sync_push_idempotency",
    "audit_logs",
    "refresh_tokens",
    "mcp_call_logs",
)


def vacuum_into(
    db: Session,
    target_path: str | Path,
    *,
    exclude_tables: tuple[str, ...] | None = DEFAULT_EXCLUDED_TABLES,
) -> None:
    """跑 VACUUM INTO,把当前数据库一致快照写到 target_path。

    exclude_tables 提供时,VACUUM 完后开 copy 文件,DROP 这些表 + 再 VACUUM
    一次释放空间。default 排除运维类表(backup_runs / audit_logs 等),
    用户数据全部保留。

    target_path 父目录必须已存在 + 文件不能已存在(SQLite 要求)。
    """
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    safe = str(target).replace("'", "''")
    db.execute(text(f"VACUUM INTO '{safe}'"))
    db.commit()
    if not target.exists():
        raise RuntimeError(f"VACUUM INTO did not produce file: {target}")

    if exclude_tables:
        from sqlalchemy import create_engine

        copy_engine = create_engine(f"sqlite:///{target}")
        try:
            with copy_engine.begin() as conn:
                for tbl in exclude_tables:
                    # 表名是常量白名单,无注入风险
                    conn.execute(text(f"DROP TABLE IF EXISTS {tbl}"))
            # VACUUM 释放被 DROP 表占用的空间(SQLite 不会自动收回)
            with copy_engine.connect() as conn:
                conn.execute(text("VACUUM"))
                conn.commit()
            logger.info(
                "vacuum_into: excluded %d tables: %s",
                len(exclude_tables), ", ".join(exclude_tables),
            )
        finally:
            copy_engine.dispose()

    size = target.stat().st_size
    logger.info("VACUUM INTO done: %s (%d bytes)", target, size)
