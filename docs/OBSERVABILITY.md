# Observability

## Endpoints

- Liveness: `/healthz`
- Readiness: `/ready`
- Metrics: `/metrics`

## Key metrics

- `beecount_http_requests_total`
- `beecount_http_errors_total`
- `beecount_http_status_2xx_total` / `4xx` / `5xx`
- `beecount_sync_push_requests_total`
- `beecount_sync_push_failed_total`
- `beecount_sync_pull_requests_total`
- `beecount_sync_pull_failed_total`
- `beecount_online_ws_users`

## Error tracing

- Unified error payload includes:
  - `error.code`
  - `error.message`
  - `error.request_id`

Use `request_id` to correlate API errors and access logs.

## Nightly perf smoke

- Workflow: `.github/workflows/nightly-perf.yml`
- Script: `scripts/nightly_perf.py`
- Artifact output: `artifacts/nightly-perf.json`
- Trigger policy: manual only (`workflow_dispatch`), no scheduled auto-run
- Report fields:
  - `write_p95_ms`
  - `read_p95_ms`
  - `write_success_rate`
  - `write_conflict_rate`

Local run example:

```bash
python scripts/nightly_perf.py --dataset-size 1000 --read-samples 100 --output artifacts/nightly-perf.json
```
