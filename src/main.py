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
from .routers import admin, attachments, auth, devices, pats, profile, read, sync, write, ws
from .routers import admin_backup, mcp_calls, two_factor
from .routers import ai as ai_router
from .routers import import_data as import_router
from .mcp import server as mcp_server
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


# OAuth 2.0 Protected Resource Metadata(RFC 9728) — MCP 2025-06-18 spec 要求。
# Claude Code / Cursor 等客户端连 MCP server 之前会探测这个 endpoint,期望拿
# 一个**可解析**的 JSON 决定走 OAuth 还是直接用 Bearer。我们用静态 PAT,
# 没 OAuth server,所以返回 `authorization_servers=[]` + `bearer_methods_
# supported=["header"]`,告诉客户端"直接用 Authorization header 上的 Bearer
# 就行"。注意:即便不用 OAuth,这个 endpoint 也必须存在 — 否则客户端拿到
# FastAPI 默认 404(`{"detail":"Not Found"}`)会因为 schema 不匹配 (缺
# `error` 字段) 整个握手抛 ZodError 报错。
#
# 同时为 `/.well-known/oauth-protected-resource/{path:path}` 提供同样响应:
# 部分 SDK 会按 `oauth-protected-resource/<resource_path>` 形式探测。
from fastapi import Request as _Request


@app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
@app.get(
    "/.well-known/oauth-protected-resource/{_resource_path:path}",
    include_in_schema=False,
)
def oauth_protected_resource_metadata(request: _Request, _resource_path: str = "") -> dict:
    # 用 request.base_url 拼 resource canonical URI(尊重反代的 X-Forwarded-Host
    # / X-Forwarded-Proto,只要前面 uvicorn 启了 --proxy-headers)。退一步即使
    # base_url 是 `http://127.0.0.1:8080/`,也不影响 SDK 解析。
    base = str(request.base_url).rstrip("/")
    resource = f"{base}{settings.api_prefix}/mcp"
    return {
        "resource": resource,
        "authorization_servers": [],
        "bearer_methods_supported": ["header"],
        "resource_documentation": "https://github.com/TNT-Likely/BeeCount-Cloud/blob/main/docs/MCP.md",
    }


app.include_router(auth.router, prefix=f"{settings.api_prefix}/auth", tags=["auth"])
app.include_router(
    two_factor.router,
    prefix=f"{settings.api_prefix}/auth/2fa",
    tags=["2fa"],
)
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
app.include_router(pats.router, prefix=f"{settings.api_prefix}/profile/pats", tags=["pats"])
app.include_router(
    mcp_calls.router,
    prefix=f"{settings.api_prefix}/profile/mcp-calls",
    tags=["mcp-calls"],
)
app.mount(f"{settings.api_prefix}/mcp", mcp_server.app)
app.include_router(ai_router.router, prefix=f"{settings.api_prefix}/ai", tags=["ai"])
app.include_router(
    import_router.router,
    prefix=f"{settings.api_prefix}/import",
    tags=["import"],
)
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


# ============================================================================
# MCP call log retention — 每 24h 清一次 > 30 天的行,跟 APScheduler 解耦,
# 用纯 asyncio loop 避免额外依赖。loop 首次睡 24h 再跑,意味着冷启动后第一
# 次清理是次日;不影响测试(test 进程秒级退出,任务永远不触发)。
# ============================================================================


_MCP_LOG_RETENTION_DAYS = 30


@app.on_event("startup")
async def _start_mcp_log_retention() -> None:  # noqa: B008
    import asyncio

    async def _loop() -> None:
        while True:
            await asyncio.sleep(24 * 3600)
            try:
                await asyncio.to_thread(_prune_mcp_logs)
            except Exception:
                logging.getLogger(__name__).exception("mcp log retention failed")

    app.state.mcp_log_retention_task = asyncio.create_task(_loop())


def _prune_mcp_logs() -> None:
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete

    from .models import MCPCallLog

    cutoff = datetime.now(timezone.utc) - timedelta(days=_MCP_LOG_RETENTION_DAYS)
    with SessionLocal() as db:
        result = db.execute(delete(MCPCallLog).where(MCPCallLog.called_at < cutoff))
        db.commit()
        deleted = result.rowcount or 0
        if deleted:
            logging.getLogger(__name__).info("mcp: retention deleted %d old call logs", deleted)


@app.on_event("shutdown")
async def _stop_mcp_log_retention() -> None:  # noqa: B008
    task = getattr(app.state, "mcp_log_retention_task", None)
    if task is not None and not task.done():
        task.cancel()


# ============================================================================
# sync_changes 表规模观测 —— 启动时打印行数 + payload 总字节,运维肉眼
# 跟踪增长趋势。sync_changes 是 append-only log(append 不 compact),长期
# 会膨胀;详见 .docs/dashboard-anomaly-budget/plan.md 关于 compaction 的讨论。
# 当前规模阈值参考:
#   ~25k 行 / 30 MB(线上 2026-05,跨度 1 个月)
#   ~120 MB / 年(线性外推)
# >= 500k 行或 >= 200 MB 时考虑加 retention / compaction job。
# 查询本身扫一遍 sync_changes,大表上几百 ms — 一次性 startup 开销可接受。
# ============================================================================


@app.on_event("startup")
async def _log_sync_changes_size() -> None:  # noqa: B008
    from sqlalchemy import text

    try:
        with SessionLocal() as db:
            row = db.execute(text(
                "SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(payload_json)), 0) AS bytes "
                "FROM sync_changes"
            )).first()
            if row is None:
                return
            n, payload_bytes = int(row[0] or 0), int(row[1] or 0)
            logging.getLogger(__name__).info(
                "sync_changes: %d rows, payload=%.1f MB (append-only,长期膨胀 watch)",
                n, payload_bytes / 1024.0 / 1024.0,
            )
    except Exception:
        # 启动早期 DB 可能还没准备好(alembic 没跑 / 测试环境)— 不阻塞
        logging.getLogger(__name__).warning(
            "sync_changes size probe failed", exc_info=True,
        )
