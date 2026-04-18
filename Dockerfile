# BeeCount-Cloud Dockerfile
# 多阶段构建：frontend (pnpm + Vite) + Python (FastAPI + Alembic)

# ===== Stage 1: frontend 构建 =====
FROM node:20-alpine AS frontend-builder
WORKDIR /workspace/frontend
RUN corepack enable

# 先只拷 lock / workspace / 各 package.json，让依赖层可以被 cache 住。
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml /workspace/frontend/
COPY frontend/apps/web/package.json /workspace/frontend/apps/web/package.json
COPY frontend/packages/api-client/package.json /workspace/frontend/packages/api-client/package.json
COPY frontend/packages/ui/package.json /workspace/frontend/packages/ui/package.json
COPY frontend/packages/web-features/package.json /workspace/frontend/packages/web-features/package.json

RUN pnpm install --frozen-lockfile || pnpm install --no-frozen-lockfile

COPY frontend /workspace/frontend
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN pnpm -C apps/web build


# ===== Stage 2: Python 运行环境 =====
FROM python:3.12-slim

ARG VERSION=dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 系统依赖：tzdata 用于时区，curl 用于 HEALTHCHECK（比起 Python urllib 更省事）
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 先装 Python 依赖（单独一层，改业务代码时不用重装）
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 后端代码
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
COPY src /app/src
COPY server.py /app/server.py
COPY scripts /app/scripts

# 静态资源（前端构建产物）
COPY --from=frontend-builder /workspace/frontend/apps/web/dist /app/static

# 数据目录:所有持久化数据(DB / 附件 / 备份 / 头像)统一放 /data,
# 容器部署直接挂一个 volume 到 /data 就能全量备份。本地开发走 config.py
# 的相对路径默认值(./data/*),两种场景互不干扰。
RUN mkdir -p /data /app/logs
ENV APP_ENV=production \
    DATA_DIR=/data \
    DATABASE_URL=sqlite:////data/beecount.db \
    BACKUP_STORAGE_DIR=/data/backups \
    ATTACHMENT_STORAGE_DIR=/data/attachments \
    WEB_STATIC_DIR=/app/static \
    ALLOW_APP_RW_SCOPES=true

# 记下版本号便于排查
RUN echo "${VERSION}" > /app/VERSION

# 默认时区（docker run 可通过 -e TZ=... 覆盖）
ENV TZ=Asia/Shanghai

EXPOSE 8080

# 健康检查：优先打 /api/v1/healthz；若没有，退回到 /
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -fsSL http://localhost:8080/api/v1/healthz \
     || curl -fsSL http://localhost:8080/ \
     || exit 1

CMD ["sh", "-c", "alembic upgrade head && uvicorn server:app --host 0.0.0.0 --port 8080"]
