#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${1:-/data/beecount.db}"
OUT_DIR="${2:-./backups/sqlite}"
TS="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$OUT_DIR"
cp "$DB_PATH" "$OUT_DIR/beecount-${TS}.db"
echo "backup created: $OUT_DIR/beecount-${TS}.db"
