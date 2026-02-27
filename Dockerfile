FROM node:20-alpine AS frontend-builder
WORKDIR /workspace/frontend
RUN corepack enable

COPY frontend/package.json /workspace/frontend/package.json
COPY frontend/pnpm-workspace.yaml /workspace/frontend/pnpm-workspace.yaml
COPY frontend/apps/web/package.json /workspace/frontend/apps/web/package.json
COPY frontend/packages/ui/package.json /workspace/frontend/packages/ui/package.json

RUN pnpm install --no-frozen-lockfile

COPY frontend /workspace/frontend
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN pnpm -C apps/web build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN mkdir -p /data

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
COPY src /app/src
COPY server.py /app/server.py
COPY --from=frontend-builder /workspace/frontend/apps/web/dist /app/static

EXPOSE 8080

CMD ["sh", "-c", "alembic upgrade head && uvicorn server:app --host 0.0.0.0 --port 8080"]
