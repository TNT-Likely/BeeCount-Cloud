"""Restore 下载流程编排:rclone copy 远端文件 → (按格式)解开到
`<DATA_DIR>/restore/<run_id>/extracted/`。

支持两种文件格式:
  - 加密 remote: `<label>.zip`(AES-256 password-protected zip),用
    pyzipper 解 + 用 stored passphrase
  - 明文 remote: `<label>.tar.gz`,标准 tar 解包

服务端**绝不动 live data**。只往 `<DATA_DIR>/restore/<run_id>/` 写。用户拿
到 extracted 目录后自己停服 + cp/rsync 替换 live data + 启服。

状态机(写在 status.json 里,UI 通过 GET /restores 读):
  - phase='downloading' → rclone copy 进行中
  - phase='extracting'  → 解包进行中(zip / tar 都用这个 phase)
  - phase='done'        → 完成,extracted/ 内容就绪
  - phase='failed'      → 任意一步失败,errorMessage 记录原因,目录保留可重试

并发控制:同一个 run_id 同时只能一份 prepare 在跑。
"""
from __future__ import annotations

import json
import logging
import shutil
import tarfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from ...config import get_settings
from ...database import SessionLocal
from ...models import BackupRemote, BackupRun
from .rclone_config import get_age_passphrase, remote_path
from .rclone_runner import RcloneError, RcloneRunner
from .tar_builder import extract_zip


logger = logging.getLogger(__name__)


ProgressCallback = Callable[[dict[str, Any]], None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _restore_dir(run_id: int) -> Path:
    return Path(get_settings().restore_dir) / str(run_id)


def _status_path(run_id: int) -> Path:
    return _restore_dir(run_id) / "status.json"


def read_status(run_id: int) -> dict[str, Any] | None:
    p = _status_path(run_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_restores() -> list[dict[str, Any]]:
    """扫 `<DATA_DIR>/restore/` 下所有子目录,读各自的 status.json。"""
    root = Path(get_settings().restore_dir)
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        status = read_status(int(child.name)) if child.name.isdigit() else None
        if status is None:
            continue
        out.append(status)
    out.sort(key=lambda s: s.get("started_at") or "", reverse=True)
    return out


def cleanup_restore(run_id: int) -> bool:
    """删某个 restore 子目录。返回是否真的删了。"""
    d = _restore_dir(run_id)
    if not d.exists():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


def cleanup_old_restores(*, max_age_days: int = 7) -> int:
    """retention job 顺手清掉超期的 restore 目录。返回清掉的数量。"""
    root = Path(get_settings().restore_dir)
    if not root.exists():
        return 0
    cutoff = _now().timestamp() - max_age_days * 86400
    n = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            n += 1
    return n


def is_running(run_id: int) -> bool:
    s = read_status(run_id)
    if not s:
        return False
    return s.get("phase") in ("downloading", "extracting")


def prepare_restore_async(
    *,
    run_id: int,
    user_id: str,
    on_progress: ProgressCallback | None = None,
) -> None:
    """后台线程跑 restore 准备流程。立即返回。"""
    if is_running(run_id):
        raise RuntimeError(f"restore for run {run_id} is already running")

    # 清掉旧目录(允许重试)
    cleanup_restore(run_id)

    t = threading.Thread(
        target=_run_prepare,
        kwargs={"run_id": run_id, "user_id": user_id, "on_progress": on_progress},
        daemon=True,
    )
    t.start()


def _write_status(run_id: int, status: dict[str, Any]) -> None:
    d = _restore_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    _status_path(run_id).write_text(json.dumps(status, indent=2), encoding="utf-8")


def _run_prepare(
    *,
    run_id: int,
    user_id: str,
    on_progress: ProgressCallback | None,
) -> None:
    settings = get_settings()
    db = SessionLocal()

    def _push(event: dict[str, Any]) -> None:
        if on_progress is not None:
            try:
                on_progress({"runId": run_id, **event})
            except Exception:
                logger.exception("restore on_progress failed")

    status: dict[str, Any] = {
        "run_id": run_id,
        "phase": "downloading",
        "started_at": _now().isoformat(),
        "finished_at": None,
        "bytes_total": None,
        "bytes_downloaded": None,
        "error_message": None,
        "extracted_path": None,
        "source_remote_id": None,
        "source_remote_name": None,
        "backup_filename": None,
    }

    try:
        # 选第一个成功的 target remote 作为下载源
        run = db.get(BackupRun, run_id)
        if run is None or run.user_id != user_id:
            raise RuntimeError(f"run {run_id} not found")
        if run.status not in ("succeeded", "partial"):
            raise RuntimeError(f"run {run_id} has status={run.status}, not eligible for restore")
        if not run.backup_filename:
            raise RuntimeError(f"run {run_id} has no backup_filename")

        # 找一个 target.status='succeeded' 的 remote
        from ...models import BackupRunTarget

        targets = (
            db.query(BackupRunTarget)
            .filter(
                BackupRunTarget.run_id == run_id,
                BackupRunTarget.status == "succeeded",
            )
            .all()
        )
        if not targets:
            raise RuntimeError("no succeeded target found for this run")
        first = targets[0]
        remote = db.get(BackupRemote, first.remote_id)
        if remote is None:
            raise RuntimeError(f"remote {first.remote_id} no longer exists")

        # 加密 remote 上传时文件名是 `<label>.zip`,明文 `<label>.tar.gz`。
        # backup_filename 在 BackupRun 里存的是主文件名(any_encrypted ? .zip : .tar.gz),
        # 但每个 remote 实际拿什么得看自己的 encrypted 标志。
        is_encrypted = bool(getattr(remote, "encrypted", False))
        base_label = run.backup_filename
        # strip 已有后缀(.zip / .tar.gz / .tar.gz.age 老格式),拿 base
        for suffix in (".tar.gz.age", ".tar.gz", ".zip"):
            if base_label.endswith(suffix):
                base_label = base_label[: -len(suffix)]
                break
        actual_filename = (
            f"{base_label}.zip" if is_encrypted else f"{base_label}.tar.gz"
        )

        status["source_remote_id"] = remote.id
        status["source_remote_name"] = remote.name
        status["backup_filename"] = actual_filename
        _write_status(run_id, status)
        _push({"type": "restore_progress", "phase": "downloading"})

        # 下载
        rdir = _restore_dir(run_id)
        rdir.mkdir(parents=True, exist_ok=True)
        downloaded = rdir / ("raw.zip" if is_encrypted else "raw.tar.gz")
        runner = RcloneRunner(
            binary=settings.rclone_binary, config_path=settings.rclone_config_path
        )

        def _on_p(p):
            status["bytes_downloaded"] = p.bytes_transferred
            if p.bytes_total:
                status["bytes_total"] = p.bytes_total
            _push(
                {
                    "type": "restore_progress",
                    "phase": "downloading",
                    "bytesTransferred": p.bytes_transferred,
                    "bytesTotal": p.bytes_total,
                    "speed": p.speed,
                }
            )

        rc, log_text = runner.copyto(
            remote_path(remote, actual_filename),
            str(downloaded),
            on_progress=_on_p,
        )
        if rc != 0:
            raise RcloneError(f"rclone copy failed: {log_text[-500:]}")
        status["bytes_downloaded"] = downloaded.stat().st_size
        if status["bytes_total"] is None:
            status["bytes_total"] = downloaded.stat().st_size

        status["phase"] = "extracting"
        _write_status(run_id, status)
        _push({"type": "restore_progress", "phase": "extracting"})

        # 解包(按格式分支)
        extracted = rdir / "extracted"
        extracted.mkdir(parents=True, exist_ok=True)
        if is_encrypted:
            passphrase = get_age_passphrase(remote)
            if not passphrase:
                raise RuntimeError(
                    f"remote {remote.name}: encrypted=True but passphrase "
                    "not stored; download the .zip via rclone/browser and "
                    "open with macOS Archive Utility / 7-Zip / Keka"
                )
            extract_zip(downloaded, extracted, passphrase=passphrase)
        else:
            with tarfile.open(downloaded, "r:gz") as tf:
                try:
                    tf.extractall(extracted, filter="data")
                except TypeError:
                    tf.extractall(extracted)
        status["extracted_path"] = str(extracted)
        status["phase"] = "done"
        status["finished_at"] = _now().isoformat()
        _write_status(run_id, status)
        _push({"type": "restore_progress", "phase": "done"})
        logger.info("restore prepared: run=%s extracted=%s", run_id, extracted)

    except Exception as exc:
        logger.exception("restore preparation failed: run=%s", run_id)
        status["phase"] = "failed"
        status["error_message"] = str(exc)[:500]
        status["finished_at"] = _now().isoformat()
        _write_status(run_id, status)
        _push(
            {
                "type": "restore_progress",
                "phase": "failed",
                "errorMessage": str(exc)[:200],
            }
        )
    finally:
        db.close()


def check_disk_space(*, required_bytes: int) -> tuple[bool, int]:
    """返回 (够不够, free bytes)。"""
    settings = get_settings()
    target_dir = Path(settings.restore_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        usage = shutil.disk_usage(target_dir)
        return usage.free >= required_bytes, usage.free
    except OSError:
        return True, 0  # 拿不到磁盘信息就放过,server 跑起来再 fail
