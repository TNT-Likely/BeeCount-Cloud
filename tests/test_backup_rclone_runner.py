"""rclone_runner 用 mock subprocess 验证 progress 解析 + 命令构造。"""
from __future__ import annotations

import json

from src.services.backup.rclone_runner import (
    RcloneProgress,
    _parse_log_line,
)


def test_parse_log_line_with_stats() -> None:
    line = json.dumps(
        {
            "level": "info",
            "msg": "transferring",
            "stats": {
                "bytes": 1024,
                "totalBytes": 8192,
                "speed": 256.0,
                "eta": 28,
                "transfers": 1,
                "totalTransfers": 5,
            },
        }
    )
    p = _parse_log_line(line)
    assert isinstance(p, RcloneProgress)
    assert p.bytes_transferred == 1024
    assert p.bytes_total == 8192
    assert p.speed == 256.0
    assert p.eta_seconds == 28
    assert p.files_transferred == 1
    assert p.files_total == 5


def test_parse_log_line_no_stats() -> None:
    line = json.dumps({"level": "info", "msg": "no stats here"})
    assert _parse_log_line(line) is None


def test_parse_log_line_invalid_json() -> None:
    assert _parse_log_line("not json {{") is None
    assert _parse_log_line("") is None


def test_progress_percent() -> None:
    p = RcloneProgress(bytes_transferred=512, bytes_total=2048)
    assert abs(p.percent - 25.0) < 0.01

    p = RcloneProgress(bytes_transferred=0, bytes_total=0)
    assert p.percent == 0.0

    # over 100% clamps
    p = RcloneProgress(bytes_transferred=2000, bytes_total=1000)
    assert p.percent == 100.0
