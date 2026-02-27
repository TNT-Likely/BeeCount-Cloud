# BeeCount Collaborative Cloud Architecture (v1)

## Goal

BeeCount Cloud v1 uses **dual channels**:

- Collaborative channel (real-time): multi-device, multi-user, shared-ledger edits.
- Backup channel (async): DB/snapshot artifacts for disaster recovery.

These channels are intentionally separated to avoid mixing conflict resolution with backup restore points.

## Channel 1: Collaborative (real-time)

- Primary write entry:
  - `/api/v1/write/*` (entity-level web/app writes with `base_change_id` optimistic lock)
  - `/api/v1/share/*` (invite/join/role management)
- Compatibility write entry:
  - `/api/v1/sync/push` with `entity_type=ledger_snapshot` (legacy snapshot clients)
- Server behavior:
  1. Validate role and scope.
  2. Append `sync_changes`.
  3. Rebuild web projections.
  4. Broadcast websocket `sync_change`.
  5. Clients pull/read to align.

## Channel 2: Backup (async)

Backup artifacts are stored separately and do not participate in sync conflict ordering.

- Upload DB artifact:
  - `POST /api/v1/admin/backups/upload-db` (`multipart/form-data`)
- Upload snapshot artifact:
  - `POST /api/v1/admin/backups/upload-snapshot`
- List artifacts:
  - `GET /api/v1/admin/backups/artifacts`
- Existing restore path remains:
  - `POST /api/v1/admin/backups/restore` (snapshot restore)

`backup_artifacts` persists metadata:

- `id`
- `ledger_id`
- `kind` (`db` or `snapshot`)
- `checksum_sha256`
- `size_bytes`
- `created_at`
- `user_id` (creator)

## Why not direct SQL from clients

Clients should never write cloud DB directly. API writes guarantee:

- role authorization (`owner/editor/viewer`)
- idempotency control
- audit log
- websocket fan-out
- deterministic read model rebuild

## Compatibility policy

- Keep `sync/push|pull|full` for legacy snapshot clients.
- Promote `write/*` as primary collaborative path for new clients.
- Keep backup upload independent from collaborative conflict semantics.
