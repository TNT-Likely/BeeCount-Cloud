"""Restore 流程的状态机 + 隔离目录边界测试。

不实跑 rclone(没装 / 无远端),只验证:
  - status.json 写入 / 读取
  - cleanup 隔离正确
  - cleanup_old_restores 按时间过滤
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from src.config import get_settings
from src.services.backup import restore_runner


@pytest.fixture
def tmp_restore_dir(tmp_path, monkeypatch):
    """临时把 restore_dir 切到 pytest tmp_path,避免污染开发环境。"""
    monkeypatch.setattr(
        get_settings(), "restore_dir", str(tmp_path / "restore"), raising=False
    )
    # 上面 setattr 走 pydantic-settings 的 frozen 拦截。直接改环境变量更稳。
    monkeypatch.setenv("RESTORE_DIR", str(tmp_path / "restore"))
    # 清缓存的 settings,让下次 get_settings() 读新 env
    get_settings.cache_clear()
    yield tmp_path / "restore"
    get_settings.cache_clear()


def _write_status(dir_path: Path, run_id: int, status: dict) -> None:
    sub = dir_path / str(run_id)
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "status.json").write_text(json.dumps(status), encoding="utf-8")


def test_read_status_returns_none_for_missing(tmp_restore_dir):
    assert restore_runner.read_status(999) is None


def test_read_status_returns_dict(tmp_restore_dir):
    _write_status(
        tmp_restore_dir,
        42,
        {
            "run_id": 42,
            "phase": "done",
            "started_at": "2026-06-12T04:00:00+00:00",
            "finished_at": "2026-06-12T04:01:00+00:00",
        },
    )
    s = restore_runner.read_status(42)
    assert s is not None
    assert s["run_id"] == 42
    assert s["phase"] == "done"


def test_is_running_true_for_downloading(tmp_restore_dir):
    _write_status(tmp_restore_dir, 1, {"run_id": 1, "phase": "downloading"})
    assert restore_runner.is_running(1) is True


def test_is_running_false_for_done(tmp_restore_dir):
    _write_status(tmp_restore_dir, 1, {"run_id": 1, "phase": "done"})
    assert restore_runner.is_running(1) is False


def test_is_running_false_for_failed(tmp_restore_dir):
    _write_status(tmp_restore_dir, 1, {"run_id": 1, "phase": "failed"})
    assert restore_runner.is_running(1) is False


def test_list_restores_sorted_desc(tmp_restore_dir):
    _write_status(
        tmp_restore_dir,
        1,
        {"run_id": 1, "phase": "done", "started_at": "2026-06-10T04:00:00+00:00"},
    )
    _write_status(
        tmp_restore_dir,
        2,
        {"run_id": 2, "phase": "done", "started_at": "2026-06-12T04:00:00+00:00"},
    )
    _write_status(
        tmp_restore_dir,
        3,
        {"run_id": 3, "phase": "done", "started_at": "2026-06-11T04:00:00+00:00"},
    )
    items = restore_runner.list_restores()
    assert [s["run_id"] for s in items] == [2, 3, 1]


def test_list_restores_skips_non_dir_and_no_status(tmp_restore_dir):
    tmp_restore_dir.mkdir(parents=True, exist_ok=True)
    # 没 status.json 的子目录
    (tmp_restore_dir / "1").mkdir()
    # 一个有 status.json 的
    _write_status(
        tmp_restore_dir,
        2,
        {"run_id": 2, "phase": "done", "started_at": "2026-06-12T04:00:00+00:00"},
    )
    # 一个非数字的目录
    (tmp_restore_dir / "garbage").mkdir()
    items = restore_runner.list_restores()
    assert len(items) == 1
    assert items[0]["run_id"] == 2


def test_cleanup_restore_removes_dir(tmp_restore_dir):
    _write_status(tmp_restore_dir, 1, {"run_id": 1, "phase": "done"})
    sub = tmp_restore_dir / "1"
    assert sub.exists()
    ok = restore_runner.cleanup_restore(1)
    assert ok is True
    assert not sub.exists()


def test_cleanup_restore_returns_false_for_missing(tmp_restore_dir):
    assert restore_runner.cleanup_restore(999) is False


def test_cleanup_old_restores_drops_aged(tmp_restore_dir, monkeypatch):
    _write_status(tmp_restore_dir, 1, {"run_id": 1, "phase": "done"})
    _write_status(tmp_restore_dir, 2, {"run_id": 2, "phase": "done"})
    sub1 = tmp_restore_dir / "1"
    sub2 = tmp_restore_dir / "2"
    # 把 sub1 mtime 推到 10 天前
    old = time.time() - 10 * 86400
    os.utime(sub1, (old, old))
    n = restore_runner.cleanup_old_restores(max_age_days=7)
    assert n == 1
    assert not sub1.exists()
    assert sub2.exists()
