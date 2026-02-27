# Migration & Rollback

## Schema migration

Run migrations:

```bash
alembic upgrade head
```

This includes:

- Shared ledger tables: `ledger_members`, `ledger_invites`
- Projection reshape from user dimension to ledger dimension
- Sync metadata extension (`updated_by_user_id`)
- Web write extension:
  - projection `sync_id` columns for transaction/account/category/tag
  - write idempotency table `sync_push_idempotency`
  - snapshot schema v2 (`items/accounts/categories/tags` include `syncId`)
- Backup channel extension:
  - backup artifact metadata table `backup_artifacts`

## Rollback

Rollback one revision:

```bash
alembic downgrade -1
```

Rollback to initial:

```bash
alembic downgrade base
```

## Notes

- Projection tables are rebuilt (derived data). If needed, trigger a fresh snapshot push from app.
- Existing owner-ledgers are auto-bootstrapped into `ledger_members` as `owner`.
- Existing ledgers with old snapshots are upgraded lazily: first web write auto-fills missing `syncId`.
