# Deployment Guide

## 1) Default: SQLite single container

```bash
docker compose up -d --build
```

- Data volume: `beecount_data` mounted at `/data`
- Default DB URL: `sqlite:////data/beecount.db`
- Backup artifact dir: `/data/backups` (`BACKUP_STORAGE_DIR`)
- App collaboration read/device scope: `ALLOW_APP_RW_SCOPES` defaults to `true` (set `false` only if you explicitly want to restrict App RW scopes)

### Cloud timezone

The official image defaults to `TZ=Asia/Shanghai`. You can explicitly override it
under the service's `environment` section in Compose:

```yaml
environment:
  TZ: "Asia/Shanghai"
  # Optional: takes precedence over TZ for scheduling and offset-free MCP times.
  # SCHEDULER_TIMEZONE: "Asia/Shanghai"
```

Recreate the container after changing environment variables (`docker compose up -d`).
MCP transaction timestamps with explicit UTC offsets retain their actual instant;
offset-free timestamps use `SCHEDULER_TIMEZONE`, then `TZ`. See
[MCP time rules](./MCP.md#交易时间与-csv-时区) for source-timezone fallback and CSV examples.
Older MCP versions ignore these defaults when writing transactions; setting `TZ`
alone does not fix their eight-hour shift. Existing records are not rewritten.

### Project cooperation links

Web's About dialog includes a secondary **BeeCount project business cooperation**
link, and AI settings includes a provider setup guide. Both open the project's
website in a new tab. They do not change provider configuration or load advertising
scripts. The AI settings entry is a single secondary guide link.

Self-host operators can hide both links with a backend runtime setting:

```yaml
environment:
  PROJECT_PARTNERSHIPS_ENABLED: "false"
```

The default is `true`. Recreate the container to apply the setting; no frontend
rebuild is required. Public `GET /api/v1/web-config` (or the configured API prefix)
returns only this display flag. Web hides these links until configuration loads,
and also hides them if this endpoint is unavailable on an older backend.

## 2) Health checks

- Liveness: `GET /healthz`
- Readiness: `GET /ready`
- Metrics: `GET /metrics`
- Compose includes container health checks (ready probe)

## 3) Backup

SQLite:

```bash
./scripts/backup_sqlite.sh /data/beecount.db ./backups/sqlite
```

The script uses `sqlite3 .backup` (SQLite Online Backup API), which is **safe
while the server is running** and works in any journal mode. Output is always
a single clean db file — no `-wal` / `-shm` companions.

> ⚠️ Don't `cp` the raw db file: the server runs in WAL mode, and a bare `cp`
> will miss uncommitted writes still sitting in `beecount.db-wal`. Always use
> the script (or `sqlite3 .backup` directly).

For a full volume snapshot (DB + attachments + JWT secret + previous backups
all together), simply tar the `beecount_data` volume after stopping the
container, OR use `VACUUM INTO` via the in-app backup runner (admin UI →
"Backup") which integrates with rclone.

### Restore

See [ROLLBACK_SOP.md](./ROLLBACK_SOP.md) — note the **delete `-wal` / `-shm`
before overwriting** step required by WAL mode.

## 4) Security baseline

- First boot auto-generates a 32-byte `JWT_SECRET` into `/data/.jwt_secret`; override the env var if you want to manage the key yourself.
- Put the API behind your own TLS reverse proxy (Caddy / Nginx / Traefik / Cloudflare).
- Keep `/data` on persistent storage — DB, attachments, backups, and the JWT secret all live there.

## 5) App scope troubleshooting

- Symptom: App shows collaboration role as not ready or device page reports `Insufficient scope`.
- Check env: ensure `ALLOW_APP_RW_SCOPES` is not set to `false`.
- Apply changes: restart service/container, then sign out/in again in App to refresh token/session context.
- Device API defaults: `GET /api/v1/devices` now returns `view=deduped` and `active_within_days=30` by default.
  - Full sessions: `GET /api/v1/devices?view=sessions&active_within_days=0`
  - Deduped devices keep `session_count` for readability.

## 6) Self-host member management

- Web collaboration page supports direct member management by email (`add/update/remove`) without requiring invite-code flow.
- Recommended operation path for self-hosting: manage shared ledger members in Web/admin, keep App as collaboration read surface.

## 7) Minimal SOP (self-host)

- If App role shows "Permission not ready", copy diagnostics from App ledger collaboration page and verify:
  - `role_resolve_status`
  - `scope_hint`
  - `deviceId`
- Verify `ALLOW_APP_RW_SCOPES` is enabled (`true`), restart backend, then sign out/in in App.
- If device list looks too large, keep default deduped view first, then switch to all sessions only for revocation.
- If a user has local default ledger `id=1` and remote shared ledger like `ledger_1.json`, the latest App build auto-reconciles identity on startup:
  - personal ledger is remapped to a namespaced local sync id,
  - `sync_queue`/`sync_state` references are migrated automatically,
  - old snapshot path is copied to the new path best-effort when target path is empty.

## 8) Experimental collaboration policy

- Current collaboration capability is treated as **experimental** for self-host deployments.
- Keep backend API compatibility stable; avoid destructive API removal while App/UI continues to iterate.
- Recommended user-facing policy:
  - App keeps collaboration entry visible with beta warnings.
  - Shared member operations remain managed in Web/admin first.
