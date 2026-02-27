# API Write Contract (v1)

## Base path

- `/api/v1/write`

## Auth

- Required scope: `web_write`
- Access token from `/api/v1/auth/login` with `client_type=web`

## Common request fields

- `base_change_id` (required): optimistic lock version
- `request_id` (optional): business trace field

## Common response

```json
{
  "ledger_id": "ledger-web",
  "base_change_id": 12,
  "new_change_id": 13,
  "server_timestamp": "2026-02-24T12:00:00+00:00",
  "idempotency_replayed": false,
  "entity_id": "tx_xxx"
}
```

## Endpoints

- `POST /ledgers` (create ledger)
- `PATCH /ledgers/{ledger_id}/meta` (update ledger name/currency)
- `POST /ledgers/{ledger_id}/transactions`
- `PATCH /ledgers/{ledger_id}/transactions/{tx_id}`
- `DELETE /ledgers/{ledger_id}/transactions/{tx_id}`
- `POST /ledgers/{ledger_id}/accounts`
- `PATCH /ledgers/{ledger_id}/accounts/{account_id}`
- `DELETE /ledgers/{ledger_id}/accounts/{account_id}`
- `POST /ledgers/{ledger_id}/categories`
- `PATCH /ledgers/{ledger_id}/categories/{category_id}`
- `DELETE /ledgers/{ledger_id}/categories/{category_id}`
- `POST /ledgers/{ledger_id}/tags`
- `PATCH /ledgers/{ledger_id}/tags/{tag_id}`
- `DELETE /ledgers/{ledger_id}/tags/{tag_id}`

## Idempotency

- Optional header: `Idempotency-Key`.
- Key uniqueness scope: `(user_id, device_id, idempotency_key)`.
- Window: 24 hours.

## Error codes

- `WRITE_CONFLICT`
- `WRITE_VALIDATION_FAILED`
- `WRITE_ROLE_FORBIDDEN`
- `ENTITY_NOT_FOUND`
- `IDEMPOTENCY_KEY_REUSED`

## 409 conflict payload

When `base_change_id` is stale:

```json
{
  "error": {
    "code": "WRITE_CONFLICT",
    "message": "Write conflict",
    "request_id": "req_xxx"
  },
  "detail": "Write conflict",
  "latest_change_id": 16,
  "latest_server_timestamp": "2026-02-24T12:00:00+00:00"
}
```
