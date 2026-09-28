"""背景排程管理後台(§ 排程管理 Phase 5)—— `services/scheduled_jobs.py` +
`routers/admin_scheduled_jobs.py` 契約測試。

覆蓋:
1. `ensure_default_configs` 幂等補齊 10 筆預設列(測試 DB 用
   `Base.metadata.create_all` 建表,不會跑 migration data seed;新增的
   job_key 靠這個函式自我補齊,不需要另外寫 migration seed row)。
2. `GET /admin/scheduled-jobs` 列出 10 筆 + admin-only 權限。
3. `PATCH /admin/scheduled-jobs/{job_key}` 改 interval_seconds/enabled,
   重算 next_run_at。
4. `POST /admin/scheduled-jobs/{job_key}/run-now` 即時回傳摘要,DB 真的有
   反映(用 mcp_log_retention / refresh_token_retention 驗證實際刪除了
   過期行)。
5. `run_due_jobs` 到期判斷邏輯(只有到期 + enabled 的才跑)。
6. 所有 job_key 都正確對應到既有函式且被實際呼叫(mock/spy)。
7. `/internal/tasks/materialize-recurring` 舊端點行為不受影響。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import MCPCallLog, RefreshToken, ScheduledJobConfig, User
from src.services import scheduled_jobs

_TEST_SESSION: sessionmaker | None = None


def _make_client() -> TestClient:
    global _TEST_SESSION
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    _TEST_SESSION = TS

    def override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def _register_app(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "app",
            "device_name": "pytest-app",
            "platform": "app",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _login_web(client: TestClient, email: str) -> str:
    res = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _bootstrap_admin(client: TestClient, email: str) -> str:
    user_data = _register_app(client, email)
    user_id = user_data["user"]["id"]
    assert _TEST_SESSION is not None
    db = _TEST_SESSION()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        assert user is not None
        user.is_admin = True
        db.commit()
    finally:
        db.close()
    return _login_web(client, email)


def _bootstrap_non_admin(client: TestClient, email: str) -> str:
    _register_app(client, email)
    return _login_web(client, email)


def _seed_defaults() -> None:
    assert _TEST_SESSION is not None
    db = _TEST_SESSION()
    try:
        scheduled_jobs.ensure_default_configs(db)
    finally:
        db.close()


def test_ensure_default_configs_seeds_seven_jobs_idempotently():
    _client = _make_client()
    try:
        assert _TEST_SESSION is not None
        db = _TEST_SESSION()
        try:
            scheduled_jobs.ensure_default_configs(db)
            rows = db.scalars(select(ScheduledJobConfig)).all()
            assert {r.job_key for r in rows} == set(scheduled_jobs.JOB_REGISTRY.keys())
            assert len(rows) == 14
            # 再跑一次應該是 no-op,不會重複插入。
            scheduled_jobs.ensure_default_configs(db)
            rows2 = db.scalars(select(ScheduledJobConfig)).all()
            assert len(rows2) == 14
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_list_scheduled_jobs_requires_admin():
    client = _make_client()
    try:
        _seed_defaults()
        token = _bootstrap_non_admin(client, "notadmin@t.com")
        r = client.get(
            "/api/v1/admin/scheduled-jobs",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403, r.text
    finally:
        app.dependency_overrides.clear()


def test_list_scheduled_jobs_returns_seven_rows_for_admin():
    client = _make_client()
    try:
        _seed_defaults()
        token = _bootstrap_admin(client, "admin1@t.com")
        r = client.get(
            "/api/v1/admin/scheduled-jobs",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 14
        by_key = {row["job_key"]: row for row in rows}
        assert by_key["card_reward_payout"]["interval_seconds"] == 5 * 60
        assert by_key["mcp_log_retention"]["interval_seconds"] == 24 * 3600
        assert all(row["enabled"] for row in rows)
    finally:
        app.dependency_overrides.clear()


def test_update_scheduled_job_changes_interval_and_recomputes_next_run_at():
    client = _make_client()
    try:
        _seed_defaults()
        token = _bootstrap_admin(client, "admin2@t.com")
        r = client.patch(
            "/api/v1/admin/scheduled-jobs/card_reward_payout",
            headers={"Authorization": f"Bearer {token}"},
            json={"interval_seconds": 600},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["interval_seconds"] == 600
        assert body["next_run_at"] is not None
        next_run = datetime.fromisoformat(body["next_run_at"])
        assert next_run > datetime.now(timezone.utc)
        assert next_run < datetime.now(timezone.utc) + timedelta(seconds=650)
    finally:
        app.dependency_overrides.clear()


def test_update_scheduled_job_can_disable():
    client = _make_client()
    try:
        _seed_defaults()
        token = _bootstrap_admin(client, "admin3@t.com")
        r = client.patch(
            "/api/v1/admin/scheduled-jobs/debt_reminders",
            headers={"Authorization": f"Bearer {token}"},
            json={"enabled": False},
        )
        assert r.status_code == 200, r.text
        assert r.json()["enabled"] is False
    finally:
        app.dependency_overrides.clear()


def test_update_unknown_job_key_returns_404():
    client = _make_client()
    try:
        _seed_defaults()
        token = _bootstrap_admin(client, "admin4@t.com")
        r = client.patch(
            "/api/v1/admin/scheduled-jobs/not_a_real_job",
            headers={"Authorization": f"Bearer {token}"},
            json={"enabled": False},
        )
        assert r.status_code == 404, r.text
    finally:
        app.dependency_overrides.clear()


def test_run_now_returns_summary_and_reflects_in_db():
    """用 mcp_log_retention 驗證 run-now 真的執行了底層邏輯,不只是回個假摘要:
    塞一筆 31 天前的 MCPCallLog,run-now 後應該被刪掉。"""
    client = _make_client()
    try:
        _seed_defaults()
        token = _bootstrap_admin(client, "admin5@t.com")

        assert _TEST_SESSION is not None
        db = _TEST_SESSION()
        try:
            admin_user = db.query(User).filter(User.email == "admin5@t.com").first()
            assert admin_user is not None
            old_log = MCPCallLog(
                user_id=admin_user.id,
                pat_id=None,
                tool_name="test_tool",
                status="ok",
                called_at=datetime.now(timezone.utc) - timedelta(days=40),
            )
            db.add(old_log)
            db.commit()
            old_log_id = old_log.id
        finally:
            db.close()

        r = client.post(
            "/api/v1/admin/scheduled-jobs/mcp_log_retention/run-now",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["job_key"] == "mcp_log_retention"
        assert body["status"] == "ok"
        assert body["summary"]["deleted"] == 1
        assert body["last_run_at"] is not None
        assert body["next_run_at"] is not None

        db = _TEST_SESSION()
        try:
            assert db.get(MCPCallLog, old_log_id) is None
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_refresh_token_retention_deletes_only_stale_invalid_tokens():
    """`refresh_token_retention` 只刪「失效(撤銷或過期)超過 2 天」的舊列,
    still-valid 的 token 跟「剛失效、還在 2 天緩衝期內」的 token 都不能碰。"""
    client = _make_client()
    try:
        _seed_defaults()
        token = _bootstrap_admin(client, "admin6@t.com")

        assert _TEST_SESSION is not None
        db = _TEST_SESSION()
        try:
            admin_user = db.query(User).filter(User.email == "admin6@t.com").first()
            assert admin_user is not None
            now = datetime.now(timezone.utc)

            stale_expired = RefreshToken(
                user_id=admin_user.id,
                device_id=None,
                token_hash="stale_expired_hash",
                expires_at=now - timedelta(days=3),
            )
            stale_revoked = RefreshToken(
                user_id=admin_user.id,
                device_id=None,
                token_hash="stale_revoked_hash",
                expires_at=now + timedelta(days=10),
                revoked_at=now - timedelta(days=3),
            )
            fresh_revoked = RefreshToken(
                user_id=admin_user.id,
                device_id=None,
                token_hash="fresh_revoked_hash",
                expires_at=now + timedelta(days=10),
                revoked_at=now - timedelta(hours=1),
            )
            still_valid = RefreshToken(
                user_id=admin_user.id,
                device_id=None,
                token_hash="still_valid_hash",
                expires_at=now + timedelta(days=10),
            )
            db.add_all([stale_expired, stale_revoked, fresh_revoked, still_valid])
            db.commit()
            keep_ids = {fresh_revoked.id, still_valid.id}
        finally:
            db.close()

        r = client.post(
            "/api/v1/admin/scheduled-jobs/refresh_token_retention/run-now",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["job_key"] == "refresh_token_retention"
        assert body["status"] == "ok"
        assert body["summary"]["deleted"] == 2

        db = _TEST_SESSION()
        try:
            # 只看我們自己塞的 4 個 token_hash,不管 register/login 順帶產生
            # 的其他有效 refresh token(不該被這個 job 動到,但也不是這個
            # assertion 要驗證的範圍)。
            remaining = db.query(RefreshToken).filter(
                RefreshToken.token_hash.in_(
                    ["stale_expired_hash", "stale_revoked_hash", "fresh_revoked_hash", "still_valid_hash"]
                )
            ).all()
            assert {row.id for row in remaining} == keep_ids
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_run_now_requires_admin():
    client = _make_client()
    try:
        _seed_defaults()
        token = _bootstrap_non_admin(client, "notadmin2@t.com")
        r = client.post(
            "/api/v1/admin/scheduled-jobs/mcp_log_retention/run-now",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403, r.text
    finally:
        app.dependency_overrides.clear()


def test_run_due_jobs_only_runs_enabled_and_due_jobs():
    """`run_due_jobs` 只挑 enabled=True 且到期(next_run_at is None 或
    <= now)的設定跑;已到期的 disabled job 不跑,還沒到期的 enabled job
    也不跑。"""
    _client = _make_client()
    try:
        _seed_defaults()
        assert _TEST_SESSION is not None
        db = _TEST_SESSION()
        try:
            now = datetime.now(timezone.utc)
            # debt_reminders: due (next_run_at=None) -> 应该跑
            # card_due_reminders: 停用 -> 不该跑,即便到期
            card_due = db.scalar(
                select(ScheduledJobConfig).where(ScheduledJobConfig.job_key == "card_due_reminders")
            )
            card_due.enabled = False
            # card_autopay: 还没到期 -> 不该跑
            autopay = db.scalar(
                select(ScheduledJobConfig).where(ScheduledJobConfig.job_key == "card_autopay")
            )
            autopay.next_run_at = now + timedelta(hours=1)
            db.commit()

            with patch.object(
                scheduled_jobs, "JOB_REGISTRY",
                {k: (lambda db, k=k: {"ran": k}) for k in scheduled_jobs.JOB_REGISTRY},
            ):
                results = scheduled_jobs.run_due_jobs(db)

            ran_keys = {r["job_key"] for r in results}
            assert "debt_reminders" in ran_keys
            assert "card_due_reminders" not in ran_keys
            assert "card_autopay" not in ran_keys
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_all_seven_jobs_map_to_registered_handlers_and_get_called():
    """鎖住 JOB_REGISTRY 的 8 個 job_key 跟預期底層函式模組的呼叫關係,
    用 spy 確認 run_job 真的呼叫到對應函式(不是摘要造假)。"""
    _client = _make_client()
    try:
        _seed_defaults()
        assert set(scheduled_jobs.JOB_REGISTRY.keys()) == {
            "mcp_log_retention",
            "refresh_token_retention",
            "recurring_materializer",
            "debt_reminders",
            "debt_unsettled_counterparties",
            "card_due_reminders",
            "transfer_rule_materialization",
            "card_autopay",
            "card_reward_payout",
            "swipesmart_usage_backfill",
            "check_latest_app_version",
            "security_quote_close",
            "security_dividend_sync",
            "security_dividend_detector",
        }

        assert _TEST_SESSION is not None
        db = _TEST_SESSION()
        try:
            with (
                patch(
                    "src.services.recurring_materializer.materialize_all_due",
                    return_value={"recurring_transactions": 0},
                ) as mock_recurring,
                patch(
                    "src.services.recurring_materializer.materialize_due_transfer_rules",
                    return_value={"materialized": 0, "skipped_insufficient": 0},
                ) as mock_transfer,
                patch(
                    "src.services.debt_reminders.send_due_debt_reminders", return_value=0,
                ) as mock_debt,
                patch(
                    "src.services.debt_unsettled_notifications.sync_unsettled_counterparty_notifications",
                    return_value=0,
                ) as mock_debt_unsettled,
                patch(
                    "src.services.credit_card_reminders.send_due_card_reminders", return_value=0,
                ) as mock_card,
                patch(
                    "src.services.credit_card_autopay.materialize_due_card_autopay",
                    return_value={"executed": 0, "skipped_insufficient": 0},
                ) as mock_autopay,
                patch(
                    "src.services.card_reward_payout.materialize_due_card_reward_payouts",
                    return_value={"tx_payouts": 0, "period_payouts": 0},
                ) as mock_reward,
                patch(
                    "src.services.swipesmart_backfill.run_swipesmart_usage_backfill",
                    return_value={"users": 0, "accounts_attempted": 0, "accounts_succeeded": 0},
                ) as mock_swipesmart,
                patch(
                    "src.services.securities.quotes.refresh_close_quotes",
                    return_value={"markets": 0, "quotes": 0},
                ) as mock_security_close,
                patch(
                    "src.services.securities.dividends.sync_dividend_events",
                    return_value={"symbols": 0, "events": 0},
                ) as mock_dividend_sync,
                patch(
                    "src.services.securities.dividends.detect_pending_dividends",
                    return_value={"created": 0, "updated": 0, "removed": 0, "reopened": 0},
                ) as mock_dividend_detect,
            ):
                scheduled_jobs.run_job(db, "recurring_materializer")
                scheduled_jobs.run_job(db, "transfer_rule_materialization")
                scheduled_jobs.run_job(db, "debt_reminders")
                scheduled_jobs.run_job(db, "debt_unsettled_counterparties")
                scheduled_jobs.run_job(db, "card_due_reminders")
                scheduled_jobs.run_job(db, "card_autopay")
                scheduled_jobs.run_job(db, "card_reward_payout")
                scheduled_jobs.run_job(db, "swipesmart_usage_backfill")
                scheduled_jobs.run_job(db, "security_quote_close")
                scheduled_jobs.run_job(db, "security_dividend_sync")
                scheduled_jobs.run_job(db, "security_dividend_detector")

            mock_recurring.assert_called_once()
            mock_transfer.assert_called_once()
            mock_debt.assert_called_once()
            mock_debt_unsettled.assert_called_once()
            mock_card.assert_called_once()
            mock_autopay.assert_called_once()
            mock_reward.assert_called_once()
            mock_swipesmart.assert_called_once()
            mock_security_close.assert_called_once()
            mock_dividend_sync.assert_called_once()
            mock_dividend_detect.assert_called_once()
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_run_job_records_error_status_without_raising():
    _client = _make_client()
    try:
        _seed_defaults()
        assert _TEST_SESSION is not None
        db = _TEST_SESSION()
        try:
            with patch.object(
                scheduled_jobs, "JOB_REGISTRY",
                {**scheduled_jobs.JOB_REGISTRY, "debt_reminders": lambda db: (_ for _ in ()).throw(RuntimeError("boom"))},
            ):
                result = scheduled_jobs.run_job(db, "debt_reminders")
            assert result["status"] == "error"
            assert "boom" in result["message"]

            row = db.scalar(
                select(ScheduledJobConfig).where(ScheduledJobConfig.job_key == "debt_reminders")
            )
            assert row.last_run_status == "error"
            assert row.next_run_at is not None
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_internal_materialize_recurring_endpoint_unaffected():
    """既有的 `/internal/tasks/materialize-recurring` 手動觸發端點不受這次
    改版影響,行為維持不變(admin-only,呼叫同一批底層函式)。"""
    client = _make_client()
    try:
        _seed_defaults()
        token = _bootstrap_admin(client, "admin6@t.com")
        r = client.post(
            "/api/v1/internal/tasks/materialize-recurring",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "recurring_transactions" in body
        assert "debt_reminders" in body
        assert "card_reminders" in body
        assert "card_autopay_executed" in body
        assert "card_reward_tx_payouts" in body
    finally:
        app.dependency_overrides.clear()
