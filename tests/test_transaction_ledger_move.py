from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.database import Base, get_db
from src.main import app
from src.models import Ledger, LedgerMember, ReadTxProjection, SyncChange, User
from src.routers.write import transactions_move as moves

SOURCE = "move-source"
TARGET = "move-target"
PATH = f"/api/v1/write/ledgers/{SOURCE}/transactions/batch/move"
IMAGE = b"qa-only receipt bytes"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'move.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(engine, autoflush=False)

    def override():
        with session() as db:
            yield db

    old = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(moves, "_attachment_root", lambda: tmp_path / "files")
    monkeypatch.setattr("src.routers.attachments._attachment_root", lambda: tmp_path / "files")
    try:
        yield TestClient(app), session
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)
        engine.dispose()


def sign_in(client, device_id, *, register=False):
    action = "register" if register else "login"
    response = client.post(
        f"/api/v1/auth/{action}",
        json={
            "email": "move@example.test",
            "password": "TmpMove123!",
            "device_id": device_id,
            "client_type": "app",
            "device_name": "QA move",
            "platform": "ios",
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}", "X-Device-ID": device_id}


def push(client, headers, ledger, entity, sync_id, payload, *, later=False):
    stamp = datetime.now(timezone.utc) + timedelta(seconds=4 if later else 0)
    response = client.post(
        "/api/v1/sync/push",
        headers=headers,
        json={
            "device_id": headers["X-Device-ID"],
            "changes": [
                {
                    "ledger_id": ledger,
                    "entity_type": entity,
                    "entity_sync_id": sync_id,
                    "action": "upsert",
                    "payload": payload,
                    "updated_at": stamp.isoformat(),
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def seed(client, headers):
    for ledger in (SOURCE, TARGET):
        push(client, headers, ledger, "ledger", ledger, {"ledgerName": ledger, "currency": "CNY"})


def prepare(client, headers, *ids):
    response = client.post(
        f"{PATH}/prepare",
        headers=headers,
        json={
            "target_ledger_id": TARGET,
            "transaction_ids": ids,
        },
    )
    assert response.status_code == 200, response.text
    return {
        "target_ledger_id": TARGET,
        "items": [{k: row[k] for k in ("sync_id", "revision")} for row in response.json()],
    }


def test_move_idempotent_sync_and_preserves_shared_original(harness):
    client, session = harness
    headers = sign_in(client, str(uuid4()), register=True)
    legacy = sign_in(client, str(uuid4()))
    seed(client, headers)
    upload = client.post(
        "/api/v1/attachments/upload",
        headers=headers,
        data={"ledger_id": SOURCE},
        files={"file": ("receipt.png", IMAGE, "image/png")},
    )
    assert upload.status_code == 200, upload.text
    file_id = upload.json()["file_id"]
    payload = {
        "amount": 12.5,
        "type": "expense",
        "excludeFromBudget": True,
        "currencyCode": "USD",
        "nativeAmount": 78.25,
        "happenedAt": "2026-09-12T12:00:00Z",
        "categoryId": "global-cat",
        "accountId": "global-account",
        "tagIds": ["global-tag"],
        "attachments": [{"cloudFileId": file_id, "fileName": "receipt.png", "sortOrder": 2}],
    }
    for tx_id in ("move-one", "move-two", "keep-one"):
        push(client, headers, SOURCE, "transaction", tx_id, {"syncId": tx_id, **payload})
    plan = prepare(client, headers, "move-one", "move-two")
    headers["Idempotency-Key"] = str(uuid4())

    response = client.post(PATH, headers=headers, json=plan)
    assert response.status_code == 200, response.text
    result = response.json()
    assert client.post(PATH, headers=headers, json=plan).json() == result
    assert (
        client.post(PATH, headers=headers, json={**plan, "items": plan["items"][:1]}).status_code
        == 409
    )

    target_file_id = result["attachment_ids"][file_id]
    assert target_file_id != file_id
    for attachment in (file_id, target_file_id):
        assert client.get(f"/api/v1/attachments/{attachment}", headers=legacy).content == IMAGE

    with session() as db:
        ledgers = {row.external_id: row.id for row in db.scalars(select(Ledger))}
        rows = db.scalars(select(ReadTxProjection).order_by(ReadTxProjection.tx_index)).all()
        assert [(r.ledger_id, r.sync_id) for r in rows] == [
            (ledgers[SOURCE], "keep-one"),
            *[(ledgers[TARGET], item["target_sync_id"]) for item in result["items"]],
        ]
        for row in rows[1:]:
            assert (row.amount, row.currency_code, row.native_amount) == (12.5, "USD", 78.25)
            assert row.exclude_from_budget
            assert row.tag_sync_ids_json == '["global-tag"]'
            assert row.account_sync_id == "global-account"
            assert row.category_sync_id == "global-cat"
        assert (
            db.query(SyncChange).filter_by(action="delete", entity_type="transaction").count() == 2
        )

    legacy_pull = client.get(
        "/api/v1/sync/pull",
        headers=legacy,
        params={
            "device_id": legacy["X-Device-ID"],
        },
    ).json()["changes"]
    for item in result["items"]:
        assert any(
            e["action"] == "delete"
            and e["ledger_id"] == SOURCE
            and e["entity_sync_id"] == item["source_sync_id"]
            for e in legacy_pull
        )
        assert any(
            e["action"] == "upsert"
            and e["ledger_id"] == TARGET
            and e["entity_sync_id"] == item["target_sync_id"]
            for e in legacy_pull
        )
    stale = push(client, legacy, SOURCE, "transaction", "move-one", payload, later=True)
    assert stale["rejected"] == 1
    assert stale["conflict_samples"][0]["reason"] == "transaction_moved"


def test_conflict_in_one_row_keeps_whole_batch_in_source(harness):
    client, session = harness
    headers = sign_in(client, str(uuid4()), register=True)
    seed(client, headers)
    for tx_id in ("row-a", "row-b"):
        push(client, headers, SOURCE, "transaction", tx_id, {"amount": 10})
    plan = prepare(client, headers, "row-a", "row-b")
    push(client, headers, SOURCE, "transaction", "row-b", {"amount": 11}, later=True)
    headers["Idempotency-Key"] = str(uuid4())
    response = client.post(PATH, headers=headers, json=plan)
    assert response.status_code == 409
    assert response.json()["detail"] == "MOVE_SOURCE_CHANGED"
    with session() as db:
        ledger_id = db.scalar(select(Ledger.id).where(Ledger.external_id == SOURCE))
        assert [(r.sync_id, r.ledger_id) for r in db.scalars(select(ReadTxProjection))] == [
            ("row-a", ledger_id),
            ("row-b", ledger_id),
        ]


def test_move_rejects_shared_and_cross_currency_targets(harness):
    client, session = harness
    headers = sign_in(client, str(uuid4()), register=True)
    seed(client, headers)
    push(client, headers, SOURCE, "transaction", "row-a", {"amount": 10})
    with session.begin() as db:
        ledger = db.scalar(select(Ledger).where(Ledger.external_id == TARGET))
        ledger.currency = "USD"
    request = {"target_ledger_id": TARGET, "transaction_ids": ["row-a"]}
    assert (
        client.post(f"{PATH}/prepare", headers=headers, json=request).json()["detail"]
        == "MOVE_REQUIRES_SAME_CURRENCY"
    )
    with session.begin() as db:
        ledger = db.scalar(select(Ledger).where(Ledger.external_id == TARGET))
        ledger.currency = "CNY"
        other = User(email="member@example.test", password_hash="test")
        db.add(other)
        db.flush()
        db.add(LedgerMember(ledger_id=ledger.id, user_id=other.id, role="editor"))
    assert (
        client.post(f"{PATH}/prepare", headers=headers, json=request).json()["detail"]
        == "MOVE_REQUIRES_PRIVATE_LEDGERS"
    )
