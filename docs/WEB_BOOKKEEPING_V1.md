# Web Bookkeeping V1

## Scope

Web v1 supports remote-first bookkeeping (no browser local DB write queue):

- Ledger create + metadata update (name/currency)
- Ledger read
- Transaction CRUD
- Account CRUD
- Category CRUD
- Tag CRUD
- Share management and settings/system pages

## Data flow

1. Web calls `GET /api/v1/read/*` for query.
2. Web calls `POST/PATCH/DELETE /api/v1/write/*` for updates.
3. Server mutates `ledger_snapshot`, writes `sync_changes`, rebuilds projections.
4. Server broadcasts `sync_change` over WebSocket.
5. Web and app clients refresh via read/sync pull.

## Web route structure

- `/login`
- `/app/:ledgerId/overview`
- `/app/:ledgerId/transactions`
- `/app/:ledgerId/accounts`
- `/app/:ledgerId/categories`
- `/app/:ledgerId/tags`
- `/app/:ledgerId/share/members`
- `/app/:ledgerId/share/invites`
- `/app/:ledgerId/settings/health|devices|errors|backup`

## Concurrency

- All write requests require `base_change_id`.
- If `base_change_id` is stale, server returns `409 WRITE_CONFLICT` with:
  - `latest_change_id`
  - `latest_server_timestamp`
- UI should refresh ledger detail and retry from latest `source_change_id`.

## Permission

- Owner/Editor: transaction write.
- Owner only: account/category/tag write.
- Owner only: ledger metadata write and member/invite management.
- Viewer: read only.

## Idempotency

- Optional request header: `Idempotency-Key`.
- Same key + same payload returns cached response (`idempotency_replayed=true`).
- Same key + different payload returns `409 IDEMPOTENCY_KEY_REUSED`.

## QA strategy (current phase)

- Frontend E2E is intentionally removed in this phase.
- Gate uses backend tests + frontend unit tests + manual smoke checklist.
