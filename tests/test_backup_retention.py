"""Pure-function tests for retention algorithm."""
from __future__ import annotations

from datetime import datetime, timezone

from src.services.backup.retention import (
    RemoteFile,
    compute_retention_deletes,
    filter_backup_files,
    parse_tar_filename,
)


def _t(spec: str) -> datetime:
    return datetime.strptime(spec, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def test_parse_tar_filename_valid() -> None:
    assert parse_tar_filename("20260612-040000Z.tar.gz") == datetime(
        2026, 6, 12, 4, 0, 0, tzinfo=timezone.utc
    )


def test_parse_tar_filename_invalid() -> None:
    assert parse_tar_filename("not-a-backup.txt") is None
    assert parse_tar_filename("backup-20260612.tar.gz") is None
    assert parse_tar_filename("20260612-040000Z.tar") is None


def test_filter_backup_files_skips_dirs_and_unknown() -> None:
    items = [
        {"Name": "20260610-040000Z.tar.gz", "IsDir": False},
        {"Name": "20260611-040000Z.tar.gz", "IsDir": False},
        {"Name": "subdir", "IsDir": True},
        {"Name": "README.md", "IsDir": False},
    ]
    files = filter_backup_files(items)
    assert len(files) == 2
    assert {f.name for f in files} == {
        "20260610-040000Z.tar.gz",
        "20260611-040000Z.tar.gz",
    }


def test_compute_retention_deletes_basic() -> None:
    files = [
        RemoteFile("20260601-040000Z.tar.gz", _t("2026-06-01")),
        RemoteFile("20260605-040000Z.tar.gz", _t("2026-06-05")),
        RemoteFile("20260612-040000Z.tar.gz", _t("2026-06-12")),
    ]
    # 当前 6/15,保留 7 天 => 6/8 之前的删
    to_delete = compute_retention_deletes(
        files, retention_days=7, now=_t("2026-06-15")
    )
    assert {f.name for f in to_delete} == {
        "20260601-040000Z.tar.gz",
        "20260605-040000Z.tar.gz",
    }


def test_compute_retention_deletes_keep_at_least_one() -> None:
    """retention=1 + 全部都很老 → 至少留 1 份。"""
    files = [
        RemoteFile("20260101-040000Z.tar.gz", _t("2026-01-01")),
        RemoteFile("20260102-040000Z.tar.gz", _t("2026-01-02")),
        RemoteFile("20260103-040000Z.tar.gz", _t("2026-01-03")),
    ]
    to_delete = compute_retention_deletes(
        files, retention_days=1, now=_t("2026-06-15"), keep_at_least=1
    )
    # 最近的那条(20260103)被保留
    deleted_names = {f.name for f in to_delete}
    assert "20260103-040000Z.tar.gz" not in deleted_names
    assert len(to_delete) == 2


def test_compute_retention_deletes_empty_input() -> None:
    assert compute_retention_deletes([], retention_days=30) == []


def test_compute_retention_deletes_all_within_retention() -> None:
    files = [
        RemoteFile("20260610-040000Z.tar.gz", _t("2026-06-10")),
        RemoteFile("20260611-040000Z.tar.gz", _t("2026-06-11")),
    ]
    to_delete = compute_retention_deletes(
        files, retention_days=30, now=_t("2026-06-15")
    )
    assert to_delete == []


def test_compute_retention_deletes_zero_or_negative_clamps_to_one() -> None:
    """retention_days=0 应该被夹到 1,不许把所有备份删光。"""
    files = [
        RemoteFile("20260610-040000Z.tar.gz", _t("2026-06-10")),
        RemoteFile("20260614-040000Z.tar.gz", _t("2026-06-14")),
    ]
    to_delete = compute_retention_deletes(
        files, retention_days=0, now=_t("2026-06-15"), keep_at_least=1
    )
    # 最近的那条要留
    assert "20260614-040000Z.tar.gz" not in {f.name for f in to_delete}
