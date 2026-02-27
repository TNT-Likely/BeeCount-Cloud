from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Ledger, UserAccount, WebTransactionProjection


def _make_client() -> tuple[TestClient, sessionmaker]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), testing_session


def _register_web(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert res.status_code == 200
    return res.json()


def test_workspace_accounts_auto_dedupe_and_remap_transaction_reference() -> None:
    client, testing_session = _make_client()
    try:
        auth = _register_web(client, "owner@example.com")
        token = auth["access_token"]
        owner_user_id = auth["user"]["id"]

        create_ledger_res = client.post(
            "/api/v1/write/ledgers",
            headers={"Authorization": f"Bearer {token}"},
            json={"ledger_name": "Default", "currency": "CNY"},
        )
        assert create_ledger_res.status_code == 200
        ledger_external_id = create_ledger_res.json()["ledger_id"]

        create_account_res = client.post(
            "/api/v1/write/workspace/accounts",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "支付宝",
                "account_type": "alipay",
                "currency": "CNY",
                "initial_balance": 0,
            },
        )
        assert create_account_res.status_code == 200
        canonical_account_id = create_account_res.json()["id"]

        db = testing_session()
        try:
            ledger = db.scalar(select(Ledger).where(Ledger.external_id == ledger_external_id))
            assert ledger is not None

            duplicate = UserAccount(
                id=str(uuid4()),
                user_id=owner_user_id,
                name=" 支付宝 ",
                account_type=None,
                currency=None,
                initial_balance=None,
            )
            db.add(duplicate)
            db.flush()

            db.add(
                WebTransactionProjection(
                    ledger_id=ledger.id,
                    created_by_user_id=owner_user_id,
                    sync_id="tx_dedupe_probe",
                    tx_index=9999,
                    tx_type="expense",
                    amount=8.8,
                    happened_at=datetime.now(timezone.utc),
                    note="dedupe-probe",
                    account_name="支付宝",
                    account_id=duplicate.id,
                )
            )
            db.commit()
            duplicate_id = duplicate.id
        finally:
            db.close()

        list_accounts_res = client.get(
            "/api/v1/read/workspace/accounts",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert list_accounts_res.status_code == 200
        rows = list_accounts_res.json()
        normalized = [row["name"].strip() for row in rows]
        assert normalized.count("支付宝") == 1

        db = testing_session()
        try:
            duplicate_row = db.scalar(select(UserAccount).where(UserAccount.id == duplicate_id))
            assert duplicate_row is not None
            assert duplicate_row.deleted_at is not None

            probe = db.scalar(
                select(WebTransactionProjection).where(
                    WebTransactionProjection.sync_id == "tx_dedupe_probe"
                )
            )
            assert probe is not None
            assert probe.account_id == canonical_account_id
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()
