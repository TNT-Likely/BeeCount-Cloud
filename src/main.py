import logging
from pathlib import Path

# !!! 顺序关键 !!!
# 必须在**任何** `from .routers ...` 之前把 JWT 密钥灌进 env。部分 router
# 模块(write.py)顶层有 `settings = get_settings()`,`get_settings` 是
# @lru_cache 的 —— 首次调用会冻结当前 env 里的 JWT_SECRET。若先触发 routers
# 导入、再 ensure_jwt_secret,settings 已经缓存了默认占位符,后续 env 变更
# 不再被反映,下面 production 校验就会 raise。
from .bootstrap import ensure_jwt_secret
ensure_jwt_secret()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import text

from .config import get_settings
from .database import SessionLocal
from .error_handling import register_exception_handlers
from .logging_ring import install_ring_buffer
from .metrics import metrics
from .observability import configure_logging, install_request_middleware
from .bootstrap_admin import ensure_admin
from .routers import admin, attachments, auth, devices, profile, read, sync, write, ws
from .routers import admin_backup
from .websocket_manager import WSConnectionManager

# 日志配置提前 —— stdout handler 必须在 ensure_admin() 之前就绪,
# 否则 bootstrap 打印的"自动创建管理员账号"banner 只进 ring buffer,
# Docker `docker compose logs` 看不到(用户只能翻 /data/.initial_admin_password)。
configure_logging()
# 再把 ring buffer handler 叠加上去(admin /admin/logs 接口用)。
# basicConfig 幂等 —— 只有首次调用时它才 addHandler;第二次看到已有 handler 就跳过,
# 所以 ring buffer 这条 handler 会独立加,两个 handler 并存。
install_ring_buffer(capacity=1000)
logging.getLogger().setLevel(logging.INFO)

# 双保险:即便后续代码触发了更早的 get_settings 调用,这里清掉 lru_cache
# 让下面的 `settings = get_settings()` 读到 ensure_jwt_secret 注入的新值。
get_settings.cache_clear()
settings = get_settings()

# 数据库为空时自动建一个 admin —— Docker 部署没 Makefile,不能 `make seed-demo`,
# 这是零配置体验的最后一环。ensure_admin 内部是幂等的,第二次启动看到已有
# user 就跳过。
ensure_admin()
if settings.app_env != "development":
    if settings.is_default_jwt_secret or settings.is_weak_jwt_secret:
        raise RuntimeError("JWT_SECRET must be changed to a strong 32+ bytes value")
    if settings.has_wildcard_cors:
        raise RuntimeError("CORS_ORIGINS cannot contain wildcard '*' in non-development environments")

from .version import __version__ as _beecount_cloud_version, APP_NAME as _beecount_cloud_name

app = FastAPI(
    title=settings.app_name,
    version=_beecount_cloud_version,
    description="BeeCount Cloud v1 API",
)


# 公开版本接口:mobile / web UI 都会调用它,在设置区或 header 展示
# "BeeCount Cloud vX.Y.Z"。不需要认证 —— 版本号不敏感,且 mobile 未登录
# 状态下(登录页)也可能想告诉用户 server 版本。
@app.get(f"{settings.api_prefix}/version")
def public_version() -> dict:
    return {"name": _beecount_cloud_name, "version": _beecount_cloud_version}

app.state.ws_manager = WSConnectionManager()
install_request_middleware(app)
register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready"}
    finally:
        db.close()


@app.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics() -> str:
    return metrics.render_prometheus()


app.include_router(auth.router, prefix=f"{settings.api_prefix}/auth", tags=["auth"])
app.include_router(devices.router, prefix=f"{settings.api_prefix}/devices", tags=["devices"])
app.include_router(sync.router, prefix=f"{settings.api_prefix}/sync", tags=["sync"])
app.include_router(admin.router, prefix=f"{settings.api_prefix}/admin", tags=["admin"])
app.include_router(
    admin_backup.router,
    prefix=f"{settings.api_prefix}/admin/backup",
    tags=["admin-backup"],
)
app.include_router(read.router, prefix=f"{settings.api_prefix}/read", tags=["read"])
app.include_router(write.router, prefix=f"{settings.api_prefix}/write", tags=["write"])
app.include_router(attachments.router, prefix=f"{settings.api_prefix}/attachments", tags=["attachments"])
app.include_router(profile.router, prefix=f"{settings.api_prefix}/profile", tags=["profile"])
app.include_router(ws.router, tags=["ws"])

_static_dir = Path(settings.web_static_dir)

if _static_dir.exists():
    _index_file = _static_dir / "index.html"

    @app.get("/", include_in_schema=False)
    def serve_root() -> FileResponse:
        if _index_file.exists():
            return FileResponse(_index_file)
        raise HTTPException(status_code=404, detail="Web console not found")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str) -> FileResponse:
        protected_prefixes = ("api/", "docs", "redoc", "openapi.json", "healthz", "ws")
        if full_path.startswith(protected_prefixes):
            raise HTTPException(status_code=404, detail="Not found")

        target = _static_dir / full_path
        if target.exists() and target.is_file():
            return FileResponse(target)
        if _index_file.exists():
            return FileResponse(_index_file)
        raise HTTPException(status_code=404, detail="Web console not found")


# ============================================================================
# Backup scheduler — startup 装载,shutdown 关停。lifespan 接口避免
# on_event 的 deprecation warning。
# ============================================================================


@app.on_event("startup")
async def _start_backup_scheduler() -> None:  # noqa: B008
    import asyncio
    from .services.backup.scheduler import get_scheduler

    # 让 admin_backup.run-now 的 thread 能用 run_coroutine_threadsafe 把 WS
    # broadcast 推回主 loop。
    app.state.main_loop = asyncio.get_running_loop()

    scheduler = get_scheduler()

    def _ws_progress(user_id: str, event: dict) -> None:
        try:
            asyncio.run_coroutine_threadsafe(
                app.state.ws_manager.broadcast_to_user(user_id, event),
                app.state.main_loop,
            )
        except Exception:
            logging.getLogger(__name__).exception("scheduled backup WS push failed")

    scheduler.on_progress(_ws_progress)
    try:
        scheduler.start_from_db()
    except Exception:
        # APScheduler 未安装(test env),或 DB 还没建表 — 不阻塞启动
        logging.getLogger(__name__).warning("backup scheduler did not start", exc_info=True)


@app.on_event("shutdown")
async def _stop_backup_scheduler() -> None:  # noqa: B008
    try:
        from .services.backup.scheduler import get_scheduler

        get_scheduler().shutdown()
    except Exception:
        logging.getLogger(__name__).exception("scheduler shutdown failed")
