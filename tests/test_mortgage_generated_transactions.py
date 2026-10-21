from __future__ import annotations

import os
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadTxProjection
from src.services.mortgage import build_mortgage_schedule, yuan_to_cents

os.environ.setdefault("ALLOW_APP_RW_SCOPES", "false")
os.environ.setdefault("REGISTRATION_ENABLED", "true")


def _make_client() -> TestClient:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _register_and_login(client: TestClient, email: str) -> str:
    client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": "d-web",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "test",
        },
    )
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": "d-web",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "test",
        },
    )
    return r.json()["access_token"]


def _create_ledger(client: TestClient, token: str) -> str:
    r = client.post(
        "/api/v1/write/ledgers",
        json={"ledger_name": "home", "currency": "CNY"},
        headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
    )
    assert r.status_code == 200, r.text
    return r.json()["entity_id"]


def test_mortgage_schedule_splits_principal_and_interest():
    schedule = build_mortgage_schedule(
        principal_cents=yuan_to_cents("120000"),
        annual_rate_percent="3.6",
        term_months=12,
        start_date=date(2026, 6, 20),
        day_of_month=20,
        repayment_method="equal_principal_interest",
    )

    assert len(schedule) == 12
    assert sum(row.principal_cents for row in schedule) == 12_000_000
    assert schedule[0].interest_cents == 36_000
    assert schedule[0].principal_cents < schedule[-1].principal_cents


def test_plugins_list_includes_mortgage_schema():
    client = _make_client()
    try:
        token = _register_and_login(client, "plugins-list@test.com")
        r = client.get(
            "/api/v1/plugins",
            headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
        )

        assert r.status_code == 200, r.text
        plugins = r.json()["plugins"]
        mortgage = next(
            item for item in plugins if item["id"] == "mortgage_auto_accounting"
        )
        assert mortgage["name_i18n"]["zh-CN"] == "房贷自动记账"
        assert "principal_amount" in mortgage["input_schema"]["properties"]
    finally:
        app.dependency_overrides.clear()


def test_mortgage_plugin_creates_principal_interest_and_prepayment_transactions():
    client = _make_client()
    try:
        token = _register_and_login(client, "mortgage1@test.com")
        ledger_id = _create_ledger(client, token)
        r = client.post(
            "/api/v1/plugins/mortgage_auto_accounting/run",
            json={
                "ledger_id": ledger_id,
                "base_change_id": 0,
                "input": {
                    "loan_name": "家庭房贷",
                    "principal_amount": "120000",
                    "annual_rate_percent": "3.6",
                    "term_months": 12,
                    "start_date": "2026-06-20",
                    "day_of_month": 20,
                    "repayment_method": "equal_principal_interest",
                    "account_name": "招商银行",
                    "prepayments": [
                        {
                            "prepayment_date": "2026-09-01",
                            "amount": "10000",
                            "effect": "reduce_payment",
                        }
                    ],
                },
            },
            headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["plugin_id"] == "mortgage_auto_accounting"
        assert body["summary"]["total_principal"] == 120000.0
        assert body["summary"]["total_prepayment"] == 10000.0
        assert body["summary"]["total_interest"] > 0
        assert body["summary"]["transaction_count"] == len(body["created_sync_ids"])

        db = next(app.dependency_overrides[get_db]())
        try:
            rows = db.scalars(
                select(ReadTxProjection)
                .where(ReadTxProjection.sync_id.in_(body["created_sync_ids"]))
                .order_by(ReadTxProjection.happened_at, ReadTxProjection.note)
            ).all()
            assert len(rows) == body["summary"]["transaction_count"]
            categories = {row.category_name for row in rows}
            assert {"房贷本金", "房贷利息", "提前还款"} <= categories
            assert {row.tx_type for row in rows} == {"expense"}
            assert all((row.tags_csv or "") == "房贷" for row in rows)
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_mortgage_plugin_idempotency_replays_created_ids():
    client = _make_client()
    try:
        token = _register_and_login(client, "mortgage2@test.com")
        ledger_id = _create_ledger(client, token)
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Device-ID": "d-web",
            "Idempotency-Key": "mortgage-same-request",
        }
        payload = {
            "ledger_id": ledger_id,
            "base_change_id": 0,
            "input": {
                "loan_name": "短贷",
                "principal_amount": "30000",
                "annual_rate_percent": "0",
                "term_months": 3,
                "start_date": "2026-06-20",
                "day_of_month": 20,
                "repayment_method": "equal_principal",
            },
        }
        r1 = client.post(
            "/api/v1/plugins/mortgage_auto_accounting/run",
            json=payload,
            headers=headers,
        )
        r2 = client.post(
            "/api/v1/plugins/mortgage_auto_accounting/run",
            json=payload,
            headers=headers,
        )

        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        assert r2.json()["created_sync_ids"] == r1.json()["created_sync_ids"]
        assert r2.json()["new_change_id"] == r1.json()["new_change_id"]

        db = next(app.dependency_overrides[get_db]())
        try:
            count = len(db.scalars(select(ReadTxProjection)).all())
            assert count == r1.json()["summary"]["transaction_count"]
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()
