"""附件 storage_path 的跨平台解析。

历史背景:在 Windows 上开发/导入的数据,attachment_files.storage_path 存的是
形如 ``data\\attachments\\<user>\\<ledger>\\<xx>\\<file>`` 的**相对 + 反斜杠**路径。
迁移到 Linux 部署后:
  1. 反斜杠在 POSIX 是普通文件名字符,Path 会把它当成单个含反斜杠的文件名;
  2. 相对路径 ``data/attachments`` 是相对进程 cwd(/app),而实际文件在挂载卷
     /data/attachments(由 ATTACHMENT_STORAGE_DIR 配置)。

本模块提供统一入口 :func:`resolve_storage_path`,把任意历史形态的 storage_path
归一到当前配置的实际文件路径,同时**不改动数据库里存储的原始值**(保持数据
可回溯,未来换环境仍能被正确解析)。新写入的附件存的是绝对路径或基于配置的
路径,本函数对它们保持透传。
"""
from __future__ import annotations

import logging
from pathlib import Path, PureWindowsPath

from .config import get_settings

logger = logging.getLogger(__name__)


def _attachment_root() -> Path:
    return Path(get_settings().attachment_storage_dir).expanduser()


def resolve_storage_path(storage_path: str | None) -> Path:
    """把数据库中的 storage_path 解析为当前环境下可读的真实文件路径。

    处理顺序:
      1. 空值 → 返回 root 本身(调用方通常不会再读)。
      2. Windows 反斜杠形态 → 用 PureWindowsPath 拆出各段,再交给步骤 4。
      3. POSIX 绝对路径 → 直接返回(新数据/原生 Linux 写入)。
      4. POSIX 相对路径 → 若含 ``attachments`` 目录段,取该段**之后**的部分,
         拼到当前 ATTACHMENT_STORAGE_DIR;否则相对 cwd 返回(旧本地行为)。
    """
    if not storage_path:
        return _attachment_root()

    raw = str(storage_path)

    # 2) 统一 Windows 反斜杠为 POSIX 段
    if "\\" in raw:
        parts = [p for p in PureWindowsPath(raw).parts if p not in ("\\", "/")]
        # 去掉盘符(如 "C:")——只关心 attachments 之后的相对段
        normalized = "/".join(parts)
    else:
        normalized = raw

    # 3) POSIX 绝对路径:保持原样
    posix = Path(normalized)
    if posix.is_absolute():
        return posix

    # 4) 相对路径:定位 attachments 段,重定向到当前配置目录
    segs = [s for s in posix.parts if s not in (".", "/")]
    if "attachments" in segs:
        idx = segs.index("attachments")
        tail = segs[idx + 1:]
        return _attachment_root().joinpath(*tail)

    # 兜底:保持原有"相对 cwd"语义(例如 profile-avatars/<id> 之类)
    return posix
