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
from starlette.routing import Route
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import text

from .config import get_settings
from .database import SessionLocal
from .error_handling import register_exception_handlers
from .logging_ring import install_ring_buffer
from .metrics import metrics
from .observability import configure_logging, install_request_middleware
from .bootstrap_admin import ensure_admin
from .routers import admin, attachments, auth, devices, notifications, pats, profile, read, sync, swipesmart, write, ws
from .routers import admin_app_version, admin_backup, admin_broadcasts, admin_licenses, admin_scheduled_jobs, admin_security_data_source, internal_tasks, mcp_calls, two_factor
from .routers import license as license_router
from .routers import app_version as app_version_router
from .routers import ai as ai_router
from .routers import import_data as import_router
from .routers import invites as invites_router
from .routers import members as members_router
from .routers import member_stats as member_stats_router
from .routers import shared_resources as shared_resources_router
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
    if not settings.oidc_configured:
        # 帳號密碼登入(/auth/login、/auth/register)只在 APP_ENV=development
        # 開放,生產環境唯一的登入路徑就是 SSO —— 沒設定 OIDC 等於整個系統
        # 沒人能登入,寧可啟動就炸掉,不要讓運維上線後才發現登不進去。
        raise RuntimeError(
            "OIDC_AUTHORITY / OIDC_CLIENT_ID / OIDC_CLIENT_SECRET must be configured in "
            "non-development environments — password login is disabled, SSO is the only "
            "way to sign in."
        )

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
app.include_router(
    admin_scheduled_jobs.router,
    prefix=f"{settings.api_prefix}/admin/scheduled-jobs",
    tags=["admin-scheduled-jobs"],
)
app.include_router(
    admin_app_version.router,
    prefix=f"{settings.api_prefix}/admin/app-version-config",
    tags=["admin-app-version"],
)
app.include_router(
    admin_security_data_source.router,
    prefix=f"{settings.api_prefix}/admin/security-data-source",
    tags=["admin-security-data-source"],
)
app.include_router(
    admin_licenses.router,
    prefix=f"{settings.api_prefix}/admin/licenses",
    tags=["admin-licenses"],
)
app.include_router(
    admin_broadcasts.router,
    prefix=f"{settings.api_prefix}/admin/broadcasts",
    tags=["admin-broadcasts"],
)
app.include_router(
    license_router.router,
    prefix=f"{settings.api_prefix}/license",
    tags=["license"],
)
app.include_router(
    app_version_router.router,
    prefix=f"{settings.api_prefix}/app-version",
    tags=["app-version"],
)
app.include_router(read.router, prefix=f"{settings.api_prefix}/read", tags=["read"])
app.include_router(write.router, prefix=f"{settings.api_prefix}/write", tags=["write"])
app.include_router(attachments.router, prefix=f"{settings.api_prefix}/attachments", tags=["attachments"])
app.include_router(profile.router, prefix=f"{settings.api_prefix}/profile", tags=["profile"])
app.include_router(pats.router, prefix=f"{settings.api_prefix}/profile/pats", tags=["pats"])
app.include_router(swipesmart.router, prefix=f"{settings.api_prefix}/profile/swipesmart", tags=["swipesmart"])
app.include_router(
    mcp_calls.router,
    prefix=f"{settings.api_prefix}/profile/mcp-calls",
    tags=["mcp-calls"],
)
app.include_router(
    notifications.router,
    prefix=f"{settings.api_prefix}/notifications",
    tags=["notifications"],
)
app.include_router(
    internal_tasks.router,
    prefix=f"{settings.api_prefix}/internal",
    tags=["internal"],
)
# Streamable HTTP MCP 端点。用精确 Route(而非 app.mount)挂载:mount 对
# "无尾斜杠的根请求"(POST /api/v1/mcp)会 307 重定向到带斜杠,而内置 HTTP
# 客户端(非浏览器,如 Hermes)未必跟随 307 的 POST → 连不上。两条精确 Route
# 覆盖带/不带尾斜杠,都直接命中、无重定向。endpoint 是 ASGI app
# (PATAuthMiddleware 包 StreamableHTTPASGIApp),Starlette 对 ASGI endpoint
# 不限制 HTTP method,POST/GET/DELETE 都放行给它处理。必须在下面 SPA
# catch-all(GET /{full_path})之前注册,否则 streamable 的 GET 通道被 SPA 抢走。
for _mcp_path in (f"{settings.api_prefix}/mcp", f"{settings.api_prefix}/mcp/"):
    app.router.routes.append(Route(_mcp_path, mcp_server.app))
app.include_router(ai_router.router, prefix=f"{settings.api_prefix}/ai", tags=["ai"])
app.include_router(
    import_router.router,
    prefix=f"{settings.api_prefix}/import",
    tags=["import"],
)
app.include_router(ws.router, tags=["ws"])
# 共享账本邀请 + 成员管理 — endpoint 内部用绝对路径(/ledgers/.../invites,/invites/...),
# 所以 prefix 就是 api_prefix 不加额外段。
app.include_router(invites_router.router, prefix=settings.api_prefix, tags=["invites"])
app.include_router(members_router.router, prefix=settings.api_prefix, tags=["members"])
app.include_router(shared_resources_router.router, prefix=settings.api_prefix, tags=["shared-resources"])
app.include_router(member_stats_router.router, prefix=settings.api_prefix, tags=["member-stats"])

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
# MCP Streamable HTTP — session manager 生命周期。Starlette 的 Mount 不传播
# 子 app 的 lifespan,而 StreamableHTTPSessionManager 必须先进入 run()
# (anyio task group)才能处理请求,所以在主 app 的 startup/shutdown 手动
# 进入/退出。run() 只能进一次(内部 _has_started 保护),用 app.state 幂等
# guard 防重复。测试不触发 lifespan(TestClient 不带 with 上下文),不受影响。
# ============================================================================


@app.on_event("startup")
async def _start_mcp_streamable() -> None:  # noqa: B008
    if getattr(app.state, "_mcp_streamable_cm", None) is not None:
        return
    cm = mcp_server.mcp.session_manager.run()
    await cm.__aenter__()
    app.state._mcp_streamable_cm = cm


@app.on_event("shutdown")
async def _stop_mcp_streamable() -> None:  # noqa: B008
    cm = getattr(app.state, "_mcp_streamable_cm", None)
    if cm is not None:
        await cm.__aexit__(None, None, None)
        app.state._mcp_streamable_cm = None


# ============================================================================
# 背景排程管理後台(§ 排程管理 Phase 5)—— 原本这里是 4 條各自獨立的 asyncio
# 迴圈(mcp 日誌清理 24h / recurring materializer 24h / debt+card+transfer+
# autopay 15min / card reward payout 5min),2026-08 收斂成一張
# `ScheduledJobConfig` 設定表(`services/scheduled_jobs.py`)+ 這一條 60 秒
# 輪詢迴圈,讓 admin 可以在 `/admin/scheduled-jobs` 後台調整頻率/停用/立即
# 執行,不需要改代碼重新部署。順便修掉舊 mcp_log_retention/recurring_
# materializer 迴圈「先 sleep 才跑」的冷啟動延遲寫法——新迴圈先跑
# `run_due_jobs`(只有到期的 job 才會真的執行)再 sleep,`next_run_at` 由
# `run_job` 自己維護,不受迴圈本身的 sleep 順序影響。
# 手動觸發仍沿用既有的 `POST /internal/tasks/materialize-recurring`
# (`internal_tasks.py`,直接呼叫同一批底層函式,兩邊互不干擾)。
# ============================================================================


_SCHEDULED_JOBS_POLL_INTERVAL_SECONDS = 60


@app.on_event("startup")
async def _start_scheduled_jobs_loop() -> None:  # noqa: B008
    import asyncio

    from .services import scheduled_jobs

    # 補齊缺失的 job_key 預設列(生產環境靠 migration seed;這裡是升級後
    # 新增 job_key 時舊部署 DB 的第二道保險,幂等、開銷是一次 SELECT)。
    with SessionLocal() as db:
        scheduled_jobs.ensure_default_configs(db)

    async def _loop() -> None:
        while True:
            try:
                await asyncio.to_thread(_run_due_scheduled_jobs_once)
            except Exception:
                logging.getLogger(__name__).exception("scheduled jobs loop failed")
            await asyncio.sleep(_SCHEDULED_JOBS_POLL_INTERVAL_SECONDS)

    app.state.scheduled_jobs_task = asyncio.create_task(_loop())


def _run_due_scheduled_jobs_once() -> None:
    from .services import scheduled_jobs

    with SessionLocal() as db:
        results = scheduled_jobs.run_due_jobs(db)
        for r in results:
            logging.getLogger(__name__).info(
                "scheduled job ran: job_key=%s status=%s message=%s",
                r["job_key"], r["status"], r["message"],
            )


@app.on_event("shutdown")
async def _stop_scheduled_jobs_loop() -> None:  # noqa: B008
    task = getattr(app.state, "scheduled_jobs_task", None)
    if task is not None and not task.done():
        task.cancel()


# ============================================================================
# WebSocket 闲置连线回收(2026-08-15)—— 见 websocket_manager.py 顶部注释:
# 后端只在 receive_text() 抛异常时才会发现断线,代理/NAT/睡眠唤醒造成的
# 半开连线永远不会抛异常,会永久堆积在连线池里(生产曾观测到单用户 256
# 条)。这个背景迴圈定期清掉超过 IDLE_TIMEOUT_SECONDS 没有任何帧的连线。
# ============================================================================


@app.on_event("startup")
async def _start_ws_sweeper() -> None:  # noqa: B008
    import asyncio

    app.state.ws_sweeper_task = asyncio.create_task(app.state.ws_manager.run_sweeper())


@app.on_event("shutdown")
async def _stop_ws_sweeper() -> None:  # noqa: B008
    task = getattr(app.state, "ws_sweeper_task", None)
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


# ============================================================================
# 回饋金沖銷交易補退款關聯(2026-09-25)—— 舊版產生的沖銷交易沒有
# refundOfId,統計會把它當一般支出。冪等,補過的不會再選到;失敗不阻塞啟動。
# 見 services/card_reward_payout.py::backfill_reward_reversal_refund_links。
# ============================================================================


@app.on_event("startup")
async def _backfill_reward_reversal_refund_links() -> None:  # noqa: B008
    from datetime import datetime, timezone

    from .services.card_reward_payout import backfill_reward_reversal_refund_links

    try:
        with SessionLocal() as db:
            n = backfill_reward_reversal_refund_links(db, now=datetime.now(timezone.utc))
            db.commit()
            if n:
                logging.getLogger(__name__).info(
                    "reward reversal backfill: linked %d reversal tx(s) to their payout", n,
                )
    except Exception:
        logging.getLogger(__name__).warning(
            "reward reversal refund-link backfill failed", exc_info=True,
        )
