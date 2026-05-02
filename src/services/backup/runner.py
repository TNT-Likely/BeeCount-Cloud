"""单次备份运行的编排。流程:

1. 在 BackupRun 表里新建 run row(status='running')
2. VACUUM INTO → staging/<run_id>/db.sqlite3
3. hardlink attachments → staging/<run_id>/attachments
4. cp .jwt_secret(可选)
5. 写 meta.json
6. tar -czf staging/<run_id>.tar.gz
7. 清理 staging/<run_id>/(只留 tar.gz)
8. fan-out 推到所有 target remote(并行,每个一条 BackupRunTarget)
9. retention:每个 remote 算 cutoff 删超期(失败不影响 run.status)
10. 删本地 tar.gz
11. 更新 run.status / run.bytes_total / run.log_text
12. WS 推 backup_status 终态

进度上报:tar 阶段无法精细估计,只发 phase 切换;rclone 阶段 RcloneRunner
按字节数解析 stats 行。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from ...config import get_settings
from ...database import SessionLocal
from ...models import (
    BackupRemote,
    BackupRun,
    BackupRunTarget,
    BackupSchedule,
    BackupScheduleRemote,
)
from ...version import __version__ as APP_VERSION
from .db_snapshot import vacuum_into
from .rclone_config import (
    RcloneConfigManager,
    get_age_passphrase,
    remote_path,
    remote_root,
)
from .rclone_runner import RcloneRunner, RcloneError
from .retention import (
    compute_retention_deletes,
    filter_backup_files,
)
from .tar_builder import build_encrypted_zip, build_targz, hardlink_tree


logger = logging.getLogger(__name__)


# 进度推送回调签名 — page 层把它接到 WSConnectionManager.broadcast_to_user。
# event 是字典形式,直接走 ws.send_json。
ProgressCallback = Callable[[dict[str, Any]], None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp_label(now: datetime | None = None) -> str:
    """生成备份文件名时间戳。用 scheduler 时区(默认跟 TZ env / 系统 local tz
    一致),不再带 'Z' 后缀 — 用户在 R2 看到的文件名直接就是本地时间。
    retention 解析仍兼容老 'Z' 格式。"""
    from .scheduler import get_scheduler

    tz = get_scheduler().timezone
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is not None:
        now = now.astimezone(tz)
    return now.strftime("%Y%m%d-%H%M%S")


def run_backup(
    *,
    user_id: str,
    schedule_id: int | None,
    on_progress: ProgressCallback | None = None,
) -> int:
    """跑一次完整备份。返回 BackupRun.id。

    可由 APScheduler 调用(传 schedule_id),也可由 'run-now' API 调用(传
    None 就当一次性手动跑)。
    """
    settings = get_settings()
    db = SessionLocal()
    run_id: int | None = None
    log_lines: list[str] = []

    def _log(msg: str) -> None:
        line = f"[{_now().isoformat()}] {msg}"
        log_lines.append(line)
        logger.info("backup: %s", msg)

    def _push(event: dict[str, Any]) -> None:
        if on_progress is not None:
            try:
                on_progress(event)
            except Exception:
                logger.exception("on_progress failed")

    try:
        # ---- 解析 target remotes ----
        if schedule_id is not None:
            schedule = db.get(BackupSchedule, schedule_id)
            if schedule is None or schedule.user_id != user_id:
                raise RuntimeError(f"schedule {schedule_id} not found for user")
            mappings = (
                db.query(BackupScheduleRemote)
                .filter(BackupScheduleRemote.schedule_id == schedule_id)
                .order_by(BackupScheduleRemote.sort_order.asc())
                .all()
            )
            remote_ids = [m.remote_id for m in mappings]
        else:
            schedule = None
            # 手动 run-now 必须由 caller 决定 target — 但我们走 schedule_id
            # 为 None 时把 caller 设的 target 推下来不在这里(caller 直接跑
            # `run_backup_to_remotes`)。
            raise RuntimeError("run_backup requires schedule_id; use run_backup_to_remotes for ad-hoc")

        if not remote_ids:
            raise RuntimeError(f"schedule {schedule_id} has no target remotes")

        return _run_with_remotes(
            db=db,
            user_id=user_id,
            schedule=schedule,
            remote_ids=remote_ids,
            include_attachments=schedule.include_attachments,
            retention_days=schedule.retention_days,
            on_progress=on_progress,
            log_lines=log_lines,
            log_fn=_log,
            settings=settings,
        )
    finally:
        db.close()


def run_backup_to_remotes(
    *,
    user_id: str,
    remote_ids: list[int],
    include_attachments: bool = True,
    retention_days: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> int:
    """手动 ad-hoc 触发 — 不绑定 schedule,直接指定 target remotes。

    retention_days = None 时跳过 retention(手动备份不参与自动清理)。
    """
    settings = get_settings()
    db = SessionLocal()
    log_lines: list[str] = []

    def _log(msg: str) -> None:
        line = f"[{_now().isoformat()}] {msg}"
        log_lines.append(line)
        logger.info("backup(adhoc): %s", msg)

    try:
        return _run_with_remotes(
            db=db,
            user_id=user_id,
            schedule=None,
            remote_ids=remote_ids,
            include_attachments=include_attachments,
            retention_days=retention_days,
            on_progress=on_progress,
            log_lines=log_lines,
            log_fn=_log,
            settings=settings,
        )
    finally:
        db.close()


def _run_with_remotes(
    *,
    db: Session,
    user_id: str,
    schedule: BackupSchedule | None,
    remote_ids: list[int],
    include_attachments: bool,
    retention_days: int | None,
    on_progress: ProgressCallback | None,
    log_lines: list[str],
    log_fn: Callable[[str], None],
    settings,
) -> int:
    # ---- 创建 run row ----
    label = _timestamp_label()
    # ---- 取 remotes 决定文件名(per-target 后缀:加密 .zip / 明文 .tar.gz)----
    remotes = (
        db.query(BackupRemote)
        .filter(BackupRemote.user_id == user_id, BackupRemote.id.in_(remote_ids))
        .all()
    )
    any_encrypted = any(getattr(r, "encrypted", False) for r in remotes)
    any_plain = any(not getattr(r, "encrypted", False) for r in remotes)
    # 主 backup_filename(给 UI / restore lookup 用):优先看 remote 偏好,
    # 全加密 → .zip;全明文 → .tar.gz;混合 → 选 zip(更通用)。
    # 真实下载时 restore 按每个 remote 的 encrypted 取自己的后缀。
    if any_encrypted:
        backup_filename = f"{label}.zip"
    else:
        backup_filename = f"{label}.tar.gz"
    run = BackupRun(
        user_id=user_id,
        schedule_id=schedule.id if schedule else None,
        started_at=_now(),
        status="running",
        backup_filename=backup_filename,
    )
    db.add(run)
    db.flush()
    run_id = run.id

    # ---- 创建 per-target row ----
    targets: list[BackupRunTarget] = []
    for r in remotes:
        t = BackupRunTarget(run_id=run_id, remote_id=r.id, status="pending")
        db.add(t)
        targets.append(t)
    db.commit()

    def _push(event: dict[str, Any]) -> None:
        if on_progress is not None:
            try:
                on_progress({"runId": run_id, **event})
            except Exception:
                logger.exception("on_progress failed")

    log_fn(f"backup start, run={run_id} label={label} remotes={[r.name for r in remotes]}")
    _push({"type": "backup_progress", "phase": "starting", "label": label})

    staging_root = Path(settings.backup_staging_dir)
    staging_root.mkdir(parents=True, exist_ok=True)
    work_dir = staging_root / f"work-{run_id}"
    work_dir.mkdir(parents=True, exist_ok=True)
    tar_path = staging_root / f"{label}.tar.gz"

    bytes_total: int | None = None
    # 用于"全部上传完之后再清理 work_dir":每个加密 target 都要重新从
    # work_dir build 一份独立 zip(各自 passphrase 不同),所以 work_dir
    # 不能在 packing 阶段就清掉。
    encrypted_zips_to_cleanup: list[Path] = []

    try:
        # ---- 1. SQLite VACUUM INTO ----
        _push({"type": "backup_progress", "phase": "snapshot_db"})
        log_fn("VACUUM INTO ...")
        # 用现成 db session 跑 VACUUM 会破坏后续提交;另开一个独立 session
        # 跑这一条命令。
        snap_db = SessionLocal()
        try:
            vacuum_into(snap_db, work_dir / "db.sqlite3")
        finally:
            snap_db.close()

        # ---- 2. attachments(hardlink) ----
        if include_attachments:
            _push({"type": "backup_progress", "phase": "snapshot_attachments"})
            attach_src = Path(settings.attachment_storage_dir)
            attach_dst = work_dir / "attachments"
            if attach_src.exists():
                try:
                    n = hardlink_tree(attach_src, attach_dst)
                    log_fn(f"hardlinked {n} attachments")
                except OSError as exc:
                    # 跨文件系统 hardlink 失败,回退到 shutil.copytree
                    log_fn(f"hardlink failed ({exc}), fallback to copytree")
                    shutil.copytree(attach_src, attach_dst, dirs_exist_ok=True)
            else:
                log_fn("attachments dir absent, skipping")

        # ---- 3. .jwt_secret ----
        secret_src = Path(settings.backup_storage_dir).parent / ".jwt_secret"
        # 实际上 .jwt_secret 在 DATA_DIR 下面,跟 backup_storage_dir 平级。
        # backup_storage_dir 默认 './data/backups',parent 是 './data',所以
        # parent / '.jwt_secret' 正好。如果 ops 改过路径,这里读不到就跳过,
        # 不阻塞 backup 流程。
        if secret_src.exists():
            shutil.copy2(secret_src, work_dir / ".jwt_secret")
            log_fn("included .jwt_secret")

        # ---- 4. meta.json ----
        meta = {
            "schemaVersion": 1,
            "appVersion": APP_VERSION,
            "createdAt": _now().isoformat(),
            "scheduleId": schedule.id if schedule else None,
            "scheduleName": schedule.name if schedule else None,
            "userId": user_id,
            "includeAttachments": include_attachments,
        }
        (work_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

        # ---- 5. tar.gz(只在有非加密 target 时 build,加密 target 直接
        #      用 work_dir build 独立的 .zip)----
        _push({"type": "backup_progress", "phase": "packing"})
        if any_plain:
            bytes_total = build_targz(work_dir, tar_path)
            log_fn(f"packed: {tar_path} ({bytes_total} bytes)")
        else:
            # 都加密了,跳过 tar.gz。bytes_total 等下用第一个 zip 的体积近似。
            tar_path = None  # type: ignore[assignment]

        # ---- 6. fan-out push ----
        runner = RcloneRunner(
            binary=settings.rclone_binary,
            config_path=settings.rclone_config_path,
        )

        def _push_one(target: BackupRunTarget, remote: BackupRemote) -> None:
            sub = SessionLocal()
            # 加密 target:从 work_dir build 独立的 AES-256 zip(各自的
            # passphrase),用户下载下来双击 → OS 弹密码框 → 解开。
            zip_path: Path | None = None
            try:
                target_db = sub.get(BackupRunTarget, target.id)
                target_db.status = "running"
                target_db.started_at = _now()
                sub.commit()
                last_progress = {"bytes": 0}

                if getattr(remote, "encrypted", False):
                    passphrase = get_age_passphrase(remote)
                    if not passphrase:
                        raise RuntimeError(
                            f"remote {remote.name}: encrypted=True but passphrase "
                            "not set; configure passphrase before backup"
                        )
                    zip_path = staging_root / f"{label}.{remote.id}.zip"
                    log_fn(f"build encrypted zip for {remote.name} -> {zip_path.name}")
                    upload_size = build_encrypted_zip(work_dir, zip_path, passphrase)
                    upload_src = zip_path
                    upload_name = f"{label}.zip"
                    encrypted_zips_to_cleanup.append(zip_path)
                else:
                    if tar_path is None:
                        raise RuntimeError(
                            "internal: tar.gz not built but unencrypted target reached"
                        )
                    upload_src = tar_path
                    upload_name = tar_path.name
                    upload_size = bytes_total or 0

                def _on_p(p):
                    last_progress["bytes"] = p.bytes_transferred
                    _push(
                        {
                            "type": "backup_progress",
                            "phase": "uploading",
                            "remoteId": remote.id,
                            "remoteName": remote.name,
                            "bytesTransferred": p.bytes_transferred,
                            "bytesTotal": upload_size,
                            "speed": p.speed,
                        }
                    )

                rc, log_text = runner.copyto(
                    str(upload_src),
                    remote_path(remote, upload_name),
                    on_progress=_on_p,
                )
                target_db = sub.get(BackupRunTarget, target.id)
                target_db.finished_at = _now()
                target_db.bytes_transferred = last_progress["bytes"] or upload_size
                if rc == 0:
                    target_db.status = "succeeded"
                else:
                    target_db.status = "failed"
                    target_db.error_message = (log_text or "")[-500:]
                sub.commit()
            except Exception as exc:
                logger.exception("push to %s failed", remote.name)
                target_db = sub.get(BackupRunTarget, target.id)
                target_db.status = "failed"
                target_db.finished_at = _now()
                target_db.error_message = str(exc)[:500]
                sub.commit()
            finally:
                sub.close()

        _push({"type": "backup_progress", "phase": "fan_out_start"})
        # 并行 push,fan-out 数量一般 ≤ 3
        with ThreadPoolExecutor(max_workers=max(1, len(targets))) as pool:
            futures: list[Future] = []
            target_by_id = {t.remote_id: t for t in targets}
            for r in remotes:
                t = target_by_id[r.id]
                futures.append(pool.submit(_push_one, t, r))
            for f in futures:
                f.result()  # exceptions already logged

        # ---- 7. 算 run 总状态 ----
        db.refresh(run)
        # reload targets — populate_existing 强制刷新 identity map,因为
        # 子线程用独立 SessionLocal 更新了 target.status,主 session 缓存
        # 还是 'pending',不刷新会让 statuses 永远是 {'pending'} 然后落到
        # 'failed' 分支(实际 rclone 已经 succeed)。
        live_targets = (
            db.query(BackupRunTarget)
            .filter(BackupRunTarget.run_id == run_id)
            .populate_existing()
            .all()
        )
        statuses = {t.status for t in live_targets}
        if statuses == {"succeeded"}:
            run.status = "succeeded"
        elif "succeeded" in statuses:
            run.status = "partial"
        else:
            run.status = "failed"

        # ---- 8. retention(只在 schedule 模式且有成功 push 时跑) ----
        if (
            retention_days is not None
            and run.status in ("succeeded", "partial")
        ):
            for t in live_targets:
                if t.status != "succeeded":
                    continue
                remote = next((r for r in remotes if r.id == t.remote_id), None)
                if remote is None:
                    continue
                try:
                    items = runner.lsjson(remote_root(remote))
                    files = filter_backup_files(items)
                    to_delete = compute_retention_deletes(
                        files, retention_days=retention_days
                    )
                    for f in to_delete:
                        try:
                            runner.deletefile(remote_path(remote, f.name))
                            log_fn(f"retention deleted {remote_path(remote, f.name)}")
                        except RcloneError as exc:
                            log_fn(f"retention delete failed {remote_path(remote, f.name)}: {exc}")
                except RcloneError as exc:
                    log_fn(f"retention list failed on {remote.name}: {exc}")

        # bytes_total fallback:全加密场景 tar 没建,用第一个加密 zip 的体积代表
        if bytes_total is None and encrypted_zips_to_cleanup:
            try:
                bytes_total = encrypted_zips_to_cleanup[0].stat().st_size
            except OSError:
                pass
        run.bytes_total = bytes_total
        run.finished_at = _now()
        run.log_text = "\n".join(log_lines)[: 1024 * 1024]
        if schedule is not None:
            schedule.last_run_at = run.finished_at
            schedule.last_run_status = run.status
        db.commit()

        log_fn(f"backup done, status={run.status}")
        _push({"type": "backup_status", "status": run.status})
        return run_id

    except Exception as exc:
        logger.exception("backup run %s failed", run_id)
        run.status = "failed"
        run.finished_at = _now()
        run.error_message = str(exc)[:500]
        run.log_text = "\n".join(log_lines)[: 1024 * 1024]
        if schedule is not None:
            schedule.last_run_at = run.finished_at
            schedule.last_run_status = "failed"
        db.commit()
        _push({"type": "backup_status", "status": "failed", "error": str(exc)[:200]})
        return run_id
    finally:
        # 清理 staging:work_dir + tar.gz + 所有 per-target 加密 zip
        shutil.rmtree(work_dir, ignore_errors=True)
        if tar_path is not None and tar_path.exists():
            try:
                tar_path.unlink()
            except OSError:
                pass
        for zp in encrypted_zips_to_cleanup:
            if zp.exists():
                try:
                    zp.unlink()
                except OSError:
                    pass
