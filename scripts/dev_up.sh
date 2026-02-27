#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${MODE:-sqlite}"

cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  make setup-backend
fi

. .venv/bin/activate
alembic upgrade head

if ! command -v pnpm >/dev/null 2>&1; then
  echo "pnpm is not installed. Please install pnpm 9+ and retry."
  echo "Suggested: npm install -g pnpm"
  exit 1
fi

if [ "$MODE" = "postgres" ]; then
  docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d db
fi

cleanup() {
  if [ -n "${API_PID:-}" ] && kill -0 "$API_PID" >/dev/null 2>&1; then
    kill "$API_PID"
  fi
  if [ -n "${WEB_PID:-}" ] && kill -0 "$WEB_PID" >/dev/null 2>&1; then
    kill "$WEB_PID"
  fi
}

trap cleanup EXIT INT TERM

uvicorn server:app --reload --port 8080 &
API_PID=$!

(
  cd frontend
  if [ ! -d node_modules ]; then
    pnpm install --no-frozen-lockfile
  fi
  pnpm -C apps/web dev
) &
WEB_PID=$!

wait "$WEB_PID"
