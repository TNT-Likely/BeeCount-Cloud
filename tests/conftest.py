import os

# Keep guardrail tests deterministic regardless of local .env defaults.
os.environ.setdefault("ALLOW_APP_RW_SCOPES", "false")
