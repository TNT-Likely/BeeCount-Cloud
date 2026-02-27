#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:-./backups/postgres}"
TS="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$OUT_DIR"
docker compose -f docker-compose.yml -f docker-compose.postgres.yml exec -T db \
  pg_dump -U beecount -d beecount >"$OUT_DIR/beecount-${TS}.sql"
echo "backup created: $OUT_DIR/beecount-${TS}.sql"
