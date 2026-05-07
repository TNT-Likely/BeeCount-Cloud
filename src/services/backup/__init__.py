"""Backup service module.

详见 .docs/backup-rclone-plan.md。

模块职责:
  - rclone_config: 渲染 / 校验 rclone.conf,obscure 兼容老数据
  - rclone_runner: 封装 subprocess 调用 + JSON log 进度解析
  - db_snapshot:   SQLite VACUUM INTO 实现
  - tar_builder:   hardlink staging + tar.gz / 加密 zip(AES-256)打包
  - retention:     列出远端 → 算超期 → purge
  - runner:        编排:VACUUM → 打包(tar.gz 或加密 zip)→ fan-out push → retention → 写 DB
  - scheduler:     APScheduler 启动 + 从 DB 装载 + add_job
"""
from .rclone_config import (
    RcloneConfigManager,
    obscure_password,
    get_age_passphrase,  # 名字保留,实际是「加密口令」(给 zip AES 用)
)
from .rclone_runner import (
    RcloneError,
    RcloneRunner,
    RcloneProgress,
)
from .retention import (
    compute_retention_deletes,
)

__all__ = [
    "RcloneConfigManager",
    "obscure_password",
    "get_age_passphrase",
    "RcloneError",
    "RcloneRunner",
    "RcloneProgress",
    "compute_retention_deletes",
]
