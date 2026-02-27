# Rollback SOP

## SQLite

1. Stop app container.
2. Restore db file:
   - `cp backups/sqlite/beecount-<ts>.db /data/beecount.db`
3. Start app container.
4. Verify:
   - `GET /ready`
   - Web ledger list and one write smoke test.

## PostgreSQL

1. Stop app container.
2. Restore SQL dump:
   - `cat backups/postgres/beecount-<ts>.sql | docker compose -f docker-compose.yml -f docker-compose.postgres.yml exec -T db psql -U beecount -d beecount`
3. Start app container.
4. Verify:
   - `GET /ready`
   - one read + one write API smoke test.

## Post-check

- `GET /metrics` is available.
- `admin/sync/errors` has no new critical errors.
- If backup artifacts are used, verify:
  - `GET /api/v1/admin/backups/artifacts?ledger_id=<id>`
  - uploaded `snapshot` artifacts can be restored via `admin/backups/restore`.
