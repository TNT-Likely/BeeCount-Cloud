"""Category pull must converge after replay, including on the originating phone."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Device, SyncChange, User, UserCategoryProjection
from src.security import SCOPE_WEB_READ, create_access_token


@pytest.fixture
def replay_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add_all([
            User(id="owner", email="owner@example.test", password_hash="unused"),
            User(id="other", email="other@example.test", password_hash="unused"),
        ])
        db.flush()
        db.add(Device(id="phone", user_id="owner", name="Phone", platform="android"))
        db.commit()

    def override_db():
        with sessions() as db:
            yield db

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_db
    token, _ = create_access_token("owner", scopes=[SCOPE_WEB_READ])
    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    try:
        yield client, sessions
    finally:
        client.close()
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous
        engine.dispose()


def event(db, payload, *, user="owner", device="old-phone", action="upsert", entity="category"):
    change = SyncChange(
        user_id=user, scope="user", ledger_id=None, entity_type=entity,
        entity_sync_id="shared-id", action=action, payload_json=payload,
        updated_at=datetime.now(timezone.utc), updated_by_device_id=device,
    )
    db.add(change)
    db.flush()
    return change.change_id


def saved_icon(db, *, user="owner", icon="local_hospital", kind="material"):
    db.add(UserCategoryProjection(
        user_id=user, sync_id="shared-id", name="医疗", kind="expense",
        level=1, icon=icon, icon_type=kind,
    ))


def pull(client, *, since=0, limit=500):
    response = client.get("/api/v1/sync/pull", params={
        "since": since, "limit": limit, "device_id": "phone",
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_replay_preserves_latest_icon_on_originating_phone(replay_client):
    client, sessions = replay_client
    legacy = {"syncId": "shared-id", "name": "医疗", "kind": "expense",
              "iconType": "material", "icon": None}
    latest = {**legacy, "icon": "local_hospital"}
    with sessions() as db:
        old_id = event(db, legacy)
        new_id = event(db, latest, device="phone")
        saved_icon(db)
        db.commit()

    # Match mobile's own-device filter as well as the server's SQL filter.
    state = latest.copy()
    since = 0
    received = []
    while True:
        page = pull(client, since=since, limit=1)
        assert page["server_cursor"] > since
        for change in page["changes"]:
            received.append(change["change_id"])
            if change["updated_by_device_id"] != "phone":
                state = change["payload"]
                assert state["icon"] == "local_hospital"
        since = page["server_cursor"]
        if not page["has_more"]:
            break
    assert received == [old_id, new_id]
    assert state == latest
    assert pull(client, since=new_id)["changes"] == []
    # Enrichment and compatibility metadata never rewrite the event log.
    with sessions() as db:
        assert db.get(SyncChange, old_id).payload_json == legacy
        assert db.get(SyncChange, new_id).updated_by_device_id == "phone"


def test_existing_cursor_can_recover_already_overwritten_icon(replay_client):
    client, sessions = replay_client
    with sessions() as db:
        old_id = event(db, {"name": "医疗", "icon": None})
        new_id = event(db, {"name": "医疗", "icon": "vaccines"}, device="phone")
        db.commit()
    page = pull(client, since=old_id)
    assert page["server_cursor"] == new_id
    assert page["changes"][0]["payload"]["icon"] == "vaccines"
    assert page["changes"][0]["updated_by_device_id"] is None


@pytest.mark.parametrize("payload", [
    {"icon": "category", "iconType": "material"},
    {"icon": "train", "iconType": "material"},
    {"icon": None, "iconType": "custom", "iconCloudFileId": "file-id", "customIconPath": "icon.png"},
    {"icon": None, "iconType": "community", "communityIconId": "community-id"},
])
def test_explicit_and_custom_icons_keep_their_payload(replay_client, payload):
    client, sessions = replay_client
    with sessions() as db:
        event(db, payload)
        saved_icon(db)
        db.commit()
    assert pull(client)["changes"][0]["payload"] == payload


@pytest.mark.parametrize("icon", [None, "", "   "])
def test_legacy_backfill_is_owner_scoped_and_does_not_write_history(replay_client, icon):
    client, sessions = replay_client
    original = {"name": "医疗", "icon": icon, "iconType": "material"}
    with sessions() as db:
        change_id = event(db, original)
        saved_icon(db, icon="vaccines")
        saved_icon(db, user="other", icon="flight")
        event(db, {"name": "Private category", "icon": "flight"}, user="other")
        db.commit()
    changes = pull(client)["changes"]
    assert len(changes) == 1
    assert changes[0]["payload"]["icon"] == "vaccines"
    with sessions() as db:
        assert db.get(SyncChange, change_id).payload_json == original


def test_deleted_category_legacy_icon_uses_existing_name_mapping(replay_client):
    client, sessions = replay_client
    with sessions() as db:
        event(db, {"name": "医院", "icon": None, "iconType": "material"})
        deleted_id = event(db, {"syncId": "shared-id"}, action="delete", device="phone")
        db.commit()
    changes = pull(client)["changes"]
    assert changes[0]["payload"]["icon"] == "medical_services"
    assert changes[1]["change_id"] == deleted_id
    assert changes[1]["action"] == "delete"
    assert changes[1]["payload"] == {"syncId": "shared-id"}
    assert changes[1]["updated_by_device_id"] is None


def test_system_events_are_visible_and_other_entities_still_skip_own_device(replay_client):
    client, sessions = replay_client
    with sessions() as db:
        event(db, {"name": "Own account"}, entity="account", device="phone")
        system_id = event(db, {"name": "Server account"}, entity="account", device=None)
        db.commit()
    changes = pull(client)["changes"]
    assert [c["change_id"] for c in changes] == [system_id]
    assert changes[0]["payload"] == {"name": "Server account"}
    with sessions() as db:
        assert len(db.scalars(select(SyncChange)).all()) == 2
