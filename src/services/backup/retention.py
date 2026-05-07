"""备份保留策略 —— 列出远端 → 找超期 tar.gz → 删。

第一版只支持简单"保留最近 N 天"。GFS 进阶模式留二期。

算法纯函数 + 易测:把"列出"和"删除"作为副作用从核心算法剥离开。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo


logger = logging.getLogger(__name__)


# 备份文件名格式三种都接:
#   - 加密 remote (新):`20260612-040000.zip`(AES-256 password-protected zip)
#   - 明文 remote (新):`20260612-040000.tar.gz`(local 时间)
#   - 老格式:`20260612-040000Z.tar.gz` / `...tar.gz.age`(向后兼容,retention
#     可以删到老备份)
_TAR_NAME_RE = re.compile(
    r"^(\d{8})-(\d{6})(Z?)(?:\.zip|\.tar\.gz(?:\.age)?)$"
)


@dataclass(frozen=True)
class RemoteFile:
    """rclone lsjson 单条结果的关注子集。"""

    name: str
    timestamp: datetime  # 从 name 解析出的 UTC time


def parse_tar_filename(name: str, *, local_tz: tzinfo | None = None) -> datetime | None:
    """解析文件名时间戳:
      - 带 'Z' 后缀:UTC
      - 无后缀:认为是 local_tz(默认 scheduler timezone,即文件被生成时
        用的 tz)。如果 local_tz 不传,fallback 到系统 local tz。

    返回 tz-aware UTC datetime,方便跟 retention now() 直接比。
    """
    m = _TAR_NAME_RE.match(name)
    if not m:
        return None
    date_s, time_s, z_marker = m.group(1), m.group(2), m.group(3)
    try:
        naive = datetime.strptime(date_s + time_s, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    if z_marker == "Z":
        return naive.replace(tzinfo=timezone.utc)
    # 无 Z 后缀:本地时间。先附 tz,再 astimezone 到 UTC。
    if local_tz is None:
        try:
            from .scheduler import get_scheduler

            local_tz = get_scheduler().timezone
        except Exception:
            from datetime import datetime as _dt

            local_tz = _dt.now().astimezone().tzinfo
    return naive.replace(tzinfo=local_tz).astimezone(timezone.utc)


def filter_backup_files(items: list[dict]) -> list[RemoteFile]:
    """从 rclone lsjson 输出里挑出 backup tar.gz(忽略其它文件)。"""
    out: list[RemoteFile] = []
    for it in items:
        name = it.get("Name") or it.get("Path") or ""
        if it.get("IsDir"):
            continue
        ts = parse_tar_filename(name)
        if ts is None:
            continue
        out.append(RemoteFile(name=name, timestamp=ts))
    return out


def compute_retention_deletes(
    files: list[RemoteFile],
    *,
    retention_days: int,
    now: datetime | None = None,
    keep_at_least: int = 1,
) -> list[RemoteFile]:
    """返回应该被删除的文件列表。纯函数 — 易测。

    规则:
      - 文件 timestamp < now - retention_days → 待删
      - 但全局保留至少 `keep_at_least` 份(防 retention=0 误配把所有备份删光)
    """
    if retention_days < 1:
        retention_days = 1
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    by_time = sorted(files, key=lambda f: f.timestamp, reverse=True)
    to_delete = [f for f in by_time if f.timestamp < cutoff]
    keep_count = len(by_time) - len(to_delete)
    if keep_count < keep_at_least:
        # 从待删列表里把"最近的几条"挪回来,补齐 keep_at_least。to_delete
        # 内部按时间倒序后,头部就是最近的待删;移到 keeper 即可。
        rescue = keep_at_least - keep_count
        to_delete = to_delete[rescue:]
    return to_delete
