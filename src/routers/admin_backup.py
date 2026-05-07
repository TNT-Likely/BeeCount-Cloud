"""Admin backup endpoints: remotes / schedules / runs CRUD + run-now + test +
download rclone.conf。

挂载在 `/api/v1/admin/backup`。所有路由要求 admin scope(require_admin_user)。

设计跟 admin.py 同款 boilerplate(依赖、audit log)。单独抽文件是因为 admin.py
本身已经 1000+ 行,加进去更长 — 后期 admin.py 拆包计划里它也会是独立 module。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import require_admin_user
from ..models import (
    AuditLog,
    BackupRemote,
    BackupRun,
    BackupRunTarget,
    BackupSchedule,
    BackupScheduleRemote,
    User,
)
from ..schemas import (
    BackupRemoteCreateRequest,
    BackupRemoteOut,
    BackupRemoteTestResponse,
    BackupRemoteUpdateRequest,
    BackupRunListOut,
    BackupRunOut,
    BackupRunTargetOut,
    BackupScheduleCreateRequest,
    BackupScheduleOut,
    BackupScheduleUpdateRequest,
)
from ..services.backup import RcloneConfigManager
from ..services.backup.rclone_config import reveal_password
from ..services.backup.rclone_config import (
    AGE_PASSPHRASE_KEY,
    ALLOWED_BACKEND_FIELDS,
    SENSITIVE_FIELDS,
    sanitize_config_summary,
)
from ..services.backup.runner import run_backup_to_remotes
from ..services.backup.scheduler import get_scheduler


logger = logging.getLogger(__name__)
router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime | None) -> datetime | None:
    """SQLite + SQLAlchemy 把 DateTime(timezone=True) 列读出来 tzinfo=None。
    序列化到 JSON 时没 TZ 后缀,JS `new Date()` 按浏览器本地时间解析,导致
    UTC 15:49 在 +8 浏览器里显示成 15:49 而不是 23:49。在 build_*_out
    路径上统一把 naive datetime 标 UTC,Pydantic 输出就带 +00:00 后缀,
    前端 toLocaleString 自动按用户时区转换。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _config_manager() -> RcloneConfigManager:
    settings = get_settings()
    return RcloneConfigManager(settings.rclone_config_path, settings.rclone_binary)


def _audit(
    db: Session,
    *,
    user_id: str,
    action: str,
    metadata: dict[str, Any],
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            ledger_id=None,
            action=action,
            metadata_json=metadata,
        )
    )


def _split_config(
    backend_type: str, config: dict[str, str]
) -> tuple[dict[str, Any], dict[str, str]]:
    """把 config 拆成 (non_sensitive, sensitive_plain)。

    所有 value strip 首尾空白 — Dashboard 复制粘贴 R2 token 时常见尾部
    \\n / 空格,签名时被一起 hash 进去就触发 SignatureDoesNotMatch。

    s3 endpoint 自动归一:R2 dashboard 的"S3 API URL"是 bucket-bound 形式
    (`https://<account>.r2.cloudflarestorage.com/<bucket>`),用户复制全
    粘贴会把 bucket 拼到 endpoint 里,rclone 路径变成
    `<endpoint>/<bucket>/file` = 双 bucket,签名直接错。这里检测并 strip
    掉路径部分,只留 host。
    """
    allowed = ALLOWED_BACKEND_FIELDS.get(backend_type, set())
    bucket_field = (config.get("bucket") or "").strip() if isinstance(config.get("bucket"), str) else ""
    non_sensitive: dict[str, Any] = {}
    sensitive: dict[str, str] = {}
    for k, v in config.items():
        if k not in allowed:
            continue
        clean = v.strip() if isinstance(v, str) else v
        if not clean and clean is not False:
            continue
        # s3 endpoint 归一:strip 路径部分,只留 scheme://host
        if backend_type == "s3" and k == "endpoint" and isinstance(clean, str):
            normalized = _normalize_s3_endpoint(clean, bucket_name=bucket_field)
            if normalized != clean:
                logger.info(
                    "endpoint normalized: %r -> %r (stripped path/bucket)",
                    clean, normalized,
                )
            clean = normalized
        if k in SENSITIVE_FIELDS:
            sensitive[k] = clean
        else:
            non_sensitive[k] = clean
    provider = non_sensitive.get("provider", "")
    if backend_type == "s3" and provider in ("Cloudflare", "Other") and not non_sensitive.get("region"):
        non_sensitive["region"] = "auto"
    # 调试日志:每个字段的长度 + 起止字符,不打实际值,可看出是否有意外字符。
    logger.info(
        "remote config sanitized: backend=%s fields=%s",
        backend_type,
        {
            k: f"len={len(v)} head={v[:2]!r} tail={v[-2:]!r}"
            for k, v in {**non_sensitive, **{f"<{sk}>": "***" for sk in sensitive}}.items()
            if isinstance(v, str)
        },
    )
    return non_sensitive, sensitive


def _normalize_s3_endpoint(endpoint: str, *, bucket_name: str = "") -> str:
    """把 R2/S3 endpoint 归一成只有 scheme://host 的形式。

    - `https://x.r2.cloudflarestorage.com/beecount-test` → `https://x.r2.cloudflarestorage.com`
    - `https://x.r2.cloudflarestorage.com/beecount-test/` → `https://x.r2.cloudflarestorage.com`
    - `https://x.r2.cloudflarestorage.com/` → `https://x.r2.cloudflarestorage.com`
    """
    if not endpoint:
        return endpoint
    s = endpoint.strip()
    # 把 path 全部砍掉(含 trailing slash),只留到 host
    # 简单做:找到第三个 '/' 之后就丢
    # https://host/path  →  https://host
    if s.startswith("http://") or s.startswith("https://"):
        protocol_end = s.find("://") + 3
        slash_after_host = s.find("/", protocol_end)
        if slash_after_host > 0:
            return s[:slash_after_host]
    return s


def _build_remote_out(r: BackupRemote) -> BackupRemoteOut:
    summary = dict(r.config_summary or {})
    summary.pop("_secrets", None)
    return BackupRemoteOut(
        id=r.id,
        name=r.name,
        backend_type=r.backend_type,
        encrypted=r.encrypted,
        config_summary=summary,
        last_test_at=_utc(r.last_test_at),
        last_test_ok=r.last_test_ok,
        last_test_error=r.last_test_error,
        created_at=_utc(r.created_at),
    )


def _build_schedule_out(db: Session, s: BackupSchedule) -> BackupScheduleOut:
    remote_ids = [
        m.remote_id
        for m in db.query(BackupScheduleRemote)
        .filter(BackupScheduleRemote.schedule_id == s.id)
        .order_by(BackupScheduleRemote.sort_order.asc())
        .all()
    ]
    return BackupScheduleOut(
        id=s.id,
        name=s.name,
        cron_expr=s.cron_expr,
        retention_days=s.retention_days,
        include_attachments=s.include_attachments,
        enabled=s.enabled,
        next_run_at=_utc(s.next_run_at),
        last_run_at=_utc(s.last_run_at),
        last_run_status=s.last_run_status,
        remote_ids=remote_ids,
        created_at=_utc(s.created_at),
    )


def _build_run_out(db: Session, run: BackupRun) -> BackupRunOut:
    targets = (
        db.query(BackupRunTarget)
        .filter(BackupRunTarget.run_id == run.id)
        .order_by(BackupRunTarget.id.asc())
        .all()
    )
    remote_id_to_name: dict[int, str] = {
        r.id: r.name
        for r in db.query(BackupRemote)
        .filter(BackupRemote.id.in_([t.remote_id for t in targets] or [-1]))
        .all()
    }
    schedule_name: str | None = None
    if run.schedule_id is not None:
        sched = db.get(BackupSchedule, run.schedule_id)
        schedule_name = sched.name if sched else None
    return BackupRunOut(
        id=run.id,
        schedule_id=run.schedule_id,
        schedule_name=schedule_name,
        started_at=_utc(run.started_at),
        finished_at=_utc(run.finished_at),
        status=run.status,
        backup_filename=run.backup_filename,
        bytes_total=run.bytes_total,
        error_message=run.error_message,
        log_text=run.log_text,
        targets=[
            BackupRunTargetOut(
                id=t.id,
                remote_id=t.remote_id,
                remote_name=remote_id_to_name.get(t.remote_id),
                status=t.status,
                started_at=_utc(t.started_at),
                finished_at=_utc(t.finished_at),
                bytes_transferred=t.bytes_transferred,
                error_message=t.error_message,
            )
            for t in targets
        ],
    )


# ============================================================================
# Remotes
# ============================================================================


@router.get("/remotes", response_model=list[BackupRemoteOut])
def list_remotes(
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> list[BackupRemoteOut]:
    rows = (
        db.query(BackupRemote)
        .filter(BackupRemote.user_id == admin_user.id)
        .order_by(BackupRemote.id.asc())
        .all()
    )
    return [_build_remote_out(r) for r in rows]


@router.post("/remotes", response_model=BackupRemoteOut, status_code=201)
def create_remote(
    req: BackupRemoteCreateRequest,
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> BackupRemoteOut:
    settings = get_settings()
    if req.backend_type not in ALLOWED_BACKEND_FIELDS and req.backend_type != "crypt":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported backend_type: {req.backend_type}",
        )
    # bucket 对 s3 / b2 是必填(对象存储路径前缀)
    if req.backend_type in {"s3", "b2"}:
        bucket = (req.config.get("bucket") or "").strip()
        if not bucket:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="bucket is required for s3/b2 backends",
            )
    existing = (
        db.query(BackupRemote)
        .filter(
            BackupRemote.user_id == admin_user.id,
            BackupRemote.name == req.name,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="remote name exists")

    non_sensitive, sensitive = _split_config(req.backend_type, req.config)

    # secrets 在 DB 里**统一存明文**(rclone v1.73.5 s3 backend 不会自动
    # reveal,obscured 字符串原样当 secret → 签名错)。
    secrets: dict[str, str] = {}
    for k, v in sensitive.items():
        if not v:
            continue
        secrets[k] = v.strip() if isinstance(v, str) else v
    # age passphrase 不是 backend 字段,不写入 rclone.conf,只在备份运行时
    # 由 runner 读出来交给 age 子进程加密 tarball。开启加密但不填 passphrase
    # 直接拒绝 — 否则后续每次备份都会失败。
    if req.encrypted:
        passphrase = (req.age_passphrase or "").strip()
        if not passphrase:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="age_passphrase is required when encrypted=True",
            )
        secrets[AGE_PASSPHRASE_KEY] = passphrase

    config_summary: dict[str, Any] = sanitize_config_summary(req.backend_type, req.config)
    config_summary["_secrets"] = secrets

    row = BackupRemote(
        user_id=admin_user.id,
        name=req.name,
        backend_type=req.backend_type,
        encrypted=bool(req.encrypted),
        config_summary=config_summary,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    db.flush()

    _audit(
        db,
        user_id=admin_user.id,
        action="backup_remote_create",
        metadata={"remoteId": row.id, "name": row.name, "backend": row.backend_type},
    )
    db.commit()

    # 重写 rclone.conf 让新 remote 立即可用
    _config_manager().rewrite_from_db(db, admin_user.id)
    return _build_remote_out(row)


@router.patch("/remotes/{remote_id}", response_model=BackupRemoteOut)
def update_remote(
    remote_id: int,
    req: BackupRemoteUpdateRequest,
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> BackupRemoteOut:
    settings = get_settings()
    row = db.get(BackupRemote, remote_id)
    if row is None or row.user_id != admin_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="remote not found")

    cfg = dict(row.config_summary or {})
    secrets = dict(cfg.pop("_secrets", {}) if isinstance(cfg.get("_secrets"), dict) else {})
    cfg.pop("_secrets", None)

    if req.config is not None:
        non_sensitive, sensitive = _split_config(row.backend_type, req.config)
        cfg.update(non_sensitive)
        for k, v in sensitive.items():
            if v:
                secrets[k] = v.strip() if isinstance(v, str) else v
    if req.age_passphrase is not None:
        passphrase = req.age_passphrase.strip()
        if passphrase:
            secrets[AGE_PASSPHRASE_KEY] = passphrase
        else:
            secrets.pop(AGE_PASSPHRASE_KEY, None)

    new_summary = sanitize_config_summary(row.backend_type, cfg)
    new_summary["_secrets"] = secrets
    row.config_summary = new_summary
    # encrypted 切换 + 防御:开启加密时必须有 passphrase。
    if req.encrypted is not None:
        will_be_encrypted = bool(req.encrypted)
        if will_be_encrypted and not secrets.get(AGE_PASSPHRASE_KEY):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="age_passphrase is required when encrypted=True",
            )
        row.encrypted = will_be_encrypted
    row.updated_at = _now()
    db.flush()
    _audit(
        db,
        user_id=admin_user.id,
        action="backup_remote_update",
        metadata={"remoteId": row.id},
    )
    db.commit()
    _config_manager().rewrite_from_db(db, admin_user.id)
    return _build_remote_out(row)


@router.delete("/remotes/{remote_id}", status_code=204)
def delete_remote(
    remote_id: int,
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> None:
    row = db.get(BackupRemote, remote_id)
    if row is None or row.user_id != admin_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="remote not found")
    # 拒绝删除被 schedule 引用的 remote(M2M FK 是 RESTRICT 但前置友好提示)
    bound = (
        db.query(BackupScheduleRemote)
        .filter(BackupScheduleRemote.remote_id == remote_id)
        .first()
    )
    if bound is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="remote is referenced by schedule(s); detach them first",
        )
    db.delete(row)
    _audit(
        db,
        user_id=admin_user.id,
        action="backup_remote_delete",
        metadata={"remoteId": remote_id, "name": row.name},
    )
    db.commit()
    _config_manager().rewrite_from_db(db, admin_user.id)


@router.get("/remotes/{remote_id}/reveal")
def reveal_remote(
    remote_id: int,
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """返回该 remote 的明文配置(含 sensitive 字段),只 admin 自己能看自己的。
    rclone 'obscure' 不是真加密,admin 本来就有权看到明文,把它从 server 拉
    出来等价于 admin "再抄一次" — 不增加信任边界。给编辑表单回填用。

    审计:每次调用打 audit log。
    """
    settings = get_settings()
    row = db.get(BackupRemote, remote_id)
    if row is None or row.user_id != admin_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="remote not found")
    cfg = dict(row.config_summary or {})
    secrets = cfg.pop("_secrets", {}) if isinstance(cfg.get("_secrets"), dict) else {}
    cfg.pop("_secrets", None)

    out_config: dict[str, str] = {}
    for k, v in cfg.items():
        if k.startswith("_"):
            continue
        if k in SENSITIVE_FIELDS:
            continue  # 从 secrets 取
        out_config[k] = "" if v is None else str(v)
    # backend secrets 现在 DB 里直接是明文(老数据是 obscured,rewrite_from_db
    # 会迁移)。兼容老数据:试着 reveal,失败/不变 = 已是明文。
    # age_passphrase 单独提出来给 UI 用 — 它从来不走 obscure 路径。
    age_passphrase: str = ""
    for k, v in (secrets or {}).items():
        stored = str(v)
        if k == AGE_PASSPHRASE_KEY:
            age_passphrase = stored
            continue
        revealed = reveal_password(stored, rclone_binary=settings.rclone_binary)
        out_config[k] = revealed if (revealed and revealed != stored) else stored

    _audit(
        db,
        user_id=admin_user.id,
        action="backup_remote_reveal",
        metadata={"remoteId": remote_id, "name": row.name},
    )
    db.commit()

    return {
        "id": row.id,
        "name": row.name,
        "backend_type": row.backend_type,
        "encrypted": row.encrypted,
        "config": out_config,
        "age_passphrase": age_passphrase,
    }


@router.post("/remotes/{remote_id}/test", response_model=BackupRemoteTestResponse)
def test_remote(
    remote_id: int,
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> BackupRemoteTestResponse:
    row = db.get(BackupRemote, remote_id)
    if row is None or row.user_id != admin_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="remote not found")
    mgr = _config_manager()
    mgr.rewrite_from_db(db, admin_user.id)  # 确保 conf 是最新的
    # 用 remote_root 拼路径 — 配了 bucket(R2 / S3 / B2)就 `<name>:<bucket>/`,
    # 没配就 `<name>:`。这样 R2 scoped token(仅授权 bucket,无 ListBuckets
    # 权限)的常见场景能跑通,不会因为 lsd root 撞 SignatureDoesNotMatch。
    from ..services.backup.rclone_config import remote_root

    test_path = remote_root(row).rstrip("/")
    if not test_path.endswith(":"):
        # remote_root 返回 'name:bucket/',我们要 'name:bucket'(rclone lsd 不要尾斜杠)
        pass  # 已经 rstrip 了
    ok, error, listing = mgr.test_path(test_path)
    # 给 SignatureDoesNotMatch 加 actionable hint — R2 用户最常见的坑
    if not ok and error and "SignatureDoesNotMatch" in error:
        hints: list[str] = [
            "签名不匹配,常见原因:",
            "1) ⚠️ R2 token 没有该 bucket 权限时也会返回 SignatureDoesNotMatch"
            "(不是 AccessDenied)— 检查 token 是否授权了你填的 bucket",
            "2) Secret Access Key 复制时尾部带了空格/换行 — 重新填一次",
            "3) Region 配置错误 — Cloudflare R2 应填 'auto' 或留空",
            "4) Bucket 名称错误 — 检查是否多了空格 / 拼错",
        ]
        error = "\n".join(hints) + "\n\n--- rclone log ---\n" + error
    row.last_test_at = _now()
    row.last_test_ok = ok
    row.last_test_error = error
    db.commit()
    return BackupRemoteTestResponse(ok=ok, error=error, listing=listing)


# ============================================================================
# rclone.conf download(给 CLI 自助用户用)
# ============================================================================


@router.get("/rclone-config", response_class=PlainTextResponse)
def download_rclone_config(
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """导出当前 user 的 rclone.conf 文本(含 obscured passphrase)。

    走 admin scope + audit log。passphrase 是 obscured(混淆,不算真加密),
    严格来说不是机密 — 但 audit log 应当留痕。
    """
    mgr = _config_manager()
    mgr.rewrite_from_db(db, admin_user.id)  # 确保最新
    text_content = mgr.read_text()
    _audit(
        db,
        user_id=admin_user.id,
        action="backup_rclone_config_download",
        metadata={"size": len(text_content)},
    )
    db.commit()
    return PlainTextResponse(
        content=text_content,
        headers={
            "Content-Disposition": 'attachment; filename="rclone.conf"',
            "Content-Type": "text/plain; charset=utf-8",
        },
    )


# ============================================================================
# Schedules
# ============================================================================


@router.get("/schedules", response_model=list[BackupScheduleOut])
def list_schedules(
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> list[BackupScheduleOut]:
    rows = (
        db.query(BackupSchedule)
        .filter(BackupSchedule.user_id == admin_user.id)
        .order_by(BackupSchedule.id.asc())
        .all()
    )
    return [_build_schedule_out(db, s) for s in rows]


@router.post("/schedules", response_model=BackupScheduleOut, status_code=201)
def create_schedule(
    req: BackupScheduleCreateRequest,
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> BackupScheduleOut:
    # 校验 cron 合法
    try:
        from apscheduler.triggers.cron import CronTrigger

        CronTrigger.from_crontab(req.cron_expr)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid cron: {exc}")

    # 校验 remote_ids 都是当前 user 的
    rs = (
        db.query(BackupRemote)
        .filter(
            BackupRemote.user_id == admin_user.id, BackupRemote.id.in_(req.remote_ids)
        )
        .all()
    )
    found_ids = {r.id for r in rs}
    missing = [rid for rid in req.remote_ids if rid not in found_ids]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"remote ids not found: {missing}",
        )

    s = BackupSchedule(
        user_id=admin_user.id,
        name=req.name,
        enabled=req.enabled,
        cron_expr=req.cron_expr,
        retention_days=req.retention_days,
        include_attachments=req.include_attachments,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(s)
    db.flush()
    for idx, rid in enumerate(req.remote_ids):
        db.add(BackupScheduleRemote(schedule_id=s.id, remote_id=rid, sort_order=idx))
    _audit(
        db,
        user_id=admin_user.id,
        action="backup_schedule_create",
        metadata={"scheduleId": s.id, "name": s.name, "remotes": req.remote_ids},
    )
    db.commit()
    get_scheduler().reload()
    return _build_schedule_out(db, s)


@router.patch("/schedules/{schedule_id}", response_model=BackupScheduleOut)
def update_schedule(
    schedule_id: int,
    req: BackupScheduleUpdateRequest,
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> BackupScheduleOut:
    s = db.get(BackupSchedule, schedule_id)
    if s is None or s.user_id != admin_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found")
    if req.name is not None:
        s.name = req.name
    if req.cron_expr is not None:
        try:
            from apscheduler.triggers.cron import CronTrigger

            CronTrigger.from_crontab(req.cron_expr)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid cron: {exc}")
        s.cron_expr = req.cron_expr
    if req.retention_days is not None:
        s.retention_days = req.retention_days
    if req.include_attachments is not None:
        s.include_attachments = req.include_attachments
    if req.enabled is not None:
        s.enabled = req.enabled
    if req.remote_ids is not None:
        # 校验 remote_ids 都是当前 user 的
        rs = (
            db.query(BackupRemote)
            .filter(
                BackupRemote.user_id == admin_user.id,
                BackupRemote.id.in_(req.remote_ids),
            )
            .all()
        )
        found_ids = {r.id for r in rs}
        missing = [rid for rid in req.remote_ids if rid not in found_ids]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"remote ids not found: {missing}",
            )
        # 全量覆盖 mapping
        db.query(BackupScheduleRemote).filter(
            BackupScheduleRemote.schedule_id == s.id
        ).delete(synchronize_session=False)
        for idx, rid in enumerate(req.remote_ids):
            db.add(BackupScheduleRemote(schedule_id=s.id, remote_id=rid, sort_order=idx))
    s.updated_at = _now()
    _audit(
        db,
        user_id=admin_user.id,
        action="backup_schedule_update",
        metadata={"scheduleId": s.id},
    )
    db.commit()
    get_scheduler().reload()
    return _build_schedule_out(db, s)


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(
    schedule_id: int,
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> None:
    s = db.get(BackupSchedule, schedule_id)
    if s is None or s.user_id != admin_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found")
    # SQLite 默认 PRAGMA foreign_keys=OFF,FK ON DELETE CASCADE 不生效;显式
    # 清掉 M2M 行,行为对 SQLite / Postgres 都一致。
    db.query(BackupScheduleRemote).filter(
        BackupScheduleRemote.schedule_id == s.id
    ).delete(synchronize_session=False)
    db.delete(s)
    _audit(
        db,
        user_id=admin_user.id,
        action="backup_schedule_delete",
        metadata={"scheduleId": schedule_id},
    )
    db.commit()
    get_scheduler().reload()


@router.post("/schedules/{schedule_id}/run-now", response_model=BackupRunOut, status_code=202)
def run_schedule_now(
    schedule_id: int,
    request: Request,
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> BackupRunOut:
    """异步触发一次备份。立即返回 202 + 一个 'running' 状态的 BackupRun row,
    实际工作在后台线程跑 — 进度通过 WebSocket 推。
    """
    s = db.get(BackupSchedule, schedule_id)
    if s is None or s.user_id != admin_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found")
    remote_ids = [
        m.remote_id
        for m in db.query(BackupScheduleRemote)
        .filter(BackupScheduleRemote.schedule_id == s.id)
        .order_by(BackupScheduleRemote.sort_order.asc())
        .all()
    ]
    if not remote_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="schedule has no target remotes",
        )

    user_id = admin_user.id

    def _bg(
        user_id: str = user_id,
        remote_ids: list[int] = remote_ids,
        retention_days: int = s.retention_days,
        include_attachments: bool = s.include_attachments,
    ) -> None:
        ws_manager = request.app.state.ws_manager

        def _push(event: dict[str, Any]) -> None:
            try:
                # broadcast_to_user 是 async,这里在线程里跑只能 fire-and-forget
                # 通过 asyncio.run_coroutine_threadsafe 推到主 loop
                import asyncio

                loop = getattr(request.app.state, "main_loop", None)
                if loop is not None:
                    asyncio.run_coroutine_threadsafe(
                        ws_manager.broadcast_to_user(user_id, event), loop
                    )
            except Exception:
                logger.exception("ws push failed")

        run_backup_to_remotes(
            user_id=user_id,
            remote_ids=remote_ids,
            include_attachments=include_attachments,
            retention_days=retention_days,
            on_progress=_push,
        )

    # 用普通 thread 跑 — 不是 APScheduler,因为这是用户主动触发,跟 cron 队列无关
    t = threading.Thread(target=_bg, daemon=True)
    t.start()

    # 立刻给个 placeholder 响应(实际 BackupRun 会在 _bg 里建)
    # 因此这里直接返回一个空 run 视图;UI 通过 GET /runs 轮询或 WS 拿真实状态。
    return BackupRunOut(
        id=0,
        schedule_id=s.id,
        schedule_name=s.name,
        started_at=_now(),
        finished_at=None,
        status="running",
        backup_filename=None,
        bytes_total=None,
        error_message=None,
        log_text=None,
        targets=[],
    )


# ============================================================================
# Runs
# ============================================================================


@router.get("/runs", response_model=BackupRunListOut)
def list_runs(
    schedule_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> BackupRunListOut:
    q = db.query(BackupRun).filter(BackupRun.user_id == admin_user.id)
    if schedule_id is not None:
        q = q.filter(BackupRun.schedule_id == schedule_id)
    total = q.count()
    rows = q.order_by(BackupRun.id.desc()).limit(limit).offset(offset).all()
    return BackupRunListOut(
        items=[_build_run_out(db, r) for r in rows],
        total=total,
    )


@router.get("/runs/{run_id}", response_model=BackupRunOut)
def get_run(
    run_id: int,
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> BackupRunOut:
    run = db.get(BackupRun, run_id)
    if run is None or run.user_id != admin_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return _build_run_out(db, run)


# ============================================================================
# Restore —— 服务端下载到隔离目录,绝不动 live data。详见
# .docs/backup-rclone-plan.md Section 9。
# ============================================================================


from ..schemas import BackupRestoreListOut, BackupRestoreOut  # noqa: E402
from ..services.backup.restore_runner import (  # noqa: E402
    check_disk_space,
    cleanup_restore,
    is_running,
    list_restores,
    prepare_restore_async,
    read_status,
)


def _status_to_out(status: dict[str, Any]) -> BackupRestoreOut:
    return BackupRestoreOut(
        run_id=int(status.get("run_id") or 0),
        phase=str(status.get("phase") or "unknown"),
        started_at=status.get("started_at"),
        finished_at=status.get("finished_at"),
        bytes_total=status.get("bytes_total"),
        bytes_downloaded=status.get("bytes_downloaded"),
        error_message=status.get("error_message"),
        extracted_path=status.get("extracted_path"),
        source_remote_id=status.get("source_remote_id"),
        source_remote_name=status.get("source_remote_name"),
        backup_filename=status.get("backup_filename"),
    )


@router.post(
    "/runs/{run_id}/prepare-restore",
    response_model=BackupRestoreOut,
    status_code=202,
)
def prepare_restore(
    run_id: int,
    request: Request,
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> BackupRestoreOut:
    run = db.get(BackupRun, run_id)
    if run is None or run.user_id != admin_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    if run.status not in ("succeeded", "partial"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"run status is {run.status}, not eligible for restore",
        )
    if is_running(run_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="restore for this run is already in progress",
        )
    # 磁盘预检 — 至少要 2x bytes_total(下载 1x + 解包 1x)
    if run.bytes_total:
        ok, free = check_disk_space(required_bytes=int(run.bytes_total) * 2 + 64 * 1024 * 1024)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"insufficient disk space: need ~{int(run.bytes_total) * 2} bytes, free {free}",
            )

    # WS push helper(broadcast 到该 user)
    user_id = admin_user.id

    def _push(event: dict[str, Any]) -> None:
        try:
            import asyncio

            ws_manager = request.app.state.ws_manager
            loop = getattr(request.app.state, "main_loop", None)
            if loop is not None:
                asyncio.run_coroutine_threadsafe(
                    ws_manager.broadcast_to_user(user_id, event), loop
                )
        except Exception:
            logger.exception("ws push failed (restore)")

    prepare_restore_async(run_id=run_id, user_id=user_id, on_progress=_push)

    _audit(
        db,
        user_id=user_id,
        action="backup_restore_prepare",
        metadata={"runId": run_id},
    )
    db.commit()

    return BackupRestoreOut(
        run_id=run_id,
        phase="downloading",
        started_at=_now(),
        finished_at=None,
        bytes_total=run.bytes_total,
        bytes_downloaded=0,
        error_message=None,
        extracted_path=None,
        source_remote_id=None,
        source_remote_name=None,
        backup_filename=run.backup_filename,
    )


@router.get("/restores", response_model=BackupRestoreListOut)
def list_restores_route(
    admin_user: User = Depends(require_admin_user),
) -> BackupRestoreListOut:
    items = list_restores()
    return BackupRestoreListOut(
        items=[_status_to_out(s) for s in items],
    )


@router.get("/restores/{run_id}", response_model=BackupRestoreOut)
def get_restore(
    run_id: int,
    admin_user: User = Depends(require_admin_user),
) -> BackupRestoreOut:
    s = read_status(run_id)
    if s is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="restore not found"
        )
    return _status_to_out(s)


@router.delete("/restores/{run_id}", status_code=204)
def delete_restore(
    run_id: int,
    admin_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> None:
    if is_running(run_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot delete a restore that is still running",
        )
    cleanup_restore(run_id)
    _audit(
        db,
        user_id=admin_user.id,
        action="backup_restore_cleanup",
        metadata={"runId": run_id},
    )
    db.commit()
