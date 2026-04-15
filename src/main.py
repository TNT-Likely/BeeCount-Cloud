from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import text

from .config import get_settings
from .database import SessionLocal
from .error_handling import register_exception_handlers
from .metrics import metrics
from .observability import install_request_middleware
from .routers import admin, attachments, auth, devices, profile, read, sync, write, ws
from .websocket_manager import WSConnectionManager

settings = get_settings()
if settings.app_env != "development":
    if settings.is_default_jwt_secret or settings.is_weak_jwt_secret:
        raise RuntimeError("JWT_SECRET must be changed to a strong 32+ bytes value")
    if settings.has_wildcard_cors:
        raise RuntimeError("CORS_ORIGINS cannot contain wildcard '*' in non-development environments")

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="BeeCount Cloud v1 API",
)

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
