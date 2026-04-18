# Deployment Guide

## 1) Default: SQLite single container

```bash
docker compose up -d --build
```

- Data volume: `beecount_data` mounted at `/data`
- Default DB URL: `sqlite:////data/beecount.db`
- Backup artifact dir: `/data/backups` (`BACKUP_STORAGE_DIR`)
- App collaboration read/device scope: `ALLOW_APP_RW_SCOPES` defaults to `true` (set `false` only if you explicitly want to restrict App RW scopes)

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

Or simply snapshot the whole `beecount_data` volume — it contains DB, attachments, backups, avatars, and the JWT secret in one place.

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
