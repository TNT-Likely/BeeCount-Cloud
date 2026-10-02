"""`GET/PUT /profile/dashboard-layout`:往返、去重、上限、清空、跨 PATCH 不被覆蓋。"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


def _make_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def _hdr(client, email, device="d1", client_type="web"):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Pa$$word1!", "device_id": device,
              "client_type": client_type, "device_name": "pytest", "platform": "test"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}", "X-Device-ID": device}


URL = "/api/v1/profile/dashboard-layout"


def test_default_is_null_then_roundtrip_across_devices():
    client = _make_client()
    h1 = _hdr(client, "dl@x.com", "d1")
    assert client.get(URL, headers=h1).json() == {"layout": None}
    body = {"version": 1, "cards": [{"id": "hero", "visible": True}, {"id": "stock.holdings", "visible": False}]}
    r = client.put(URL, headers=h1, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["layout"] == body
    h2 = _hdr(client, "dl@x.com", "d2")
    assert client.get(URL, headers=h2).json()["layout"] == body


def test_dedupe_unknown_kept_and_reset():
    client = _make_client()
    h = _hdr(client, "dl2@x.com")
    r = client.put(URL, headers=h, json={"cards": [
        {"id": "a", "visible": True}, {"id": "a", "visible": False}, {"id": "future.card", "visible": True},
    ]})
    assert r.status_code == 200
    assert [c["id"] for c in r.json()["layout"]["cards"]] == ["a", "future.card"]
    assert r.json()["layout"]["cards"][0]["visible"] is True
    # 空 cards = 還原預設
    assert client.put(URL, headers=h, json={"cards": []}).json() == {"layout": None}
    assert client.get(URL, headers=h).json() == {"layout": None}


def test_limits_and_bad_ids():
    client = _make_client()
    h = _hdr(client, "dl3@x.com")
    too_many = {"cards": [{"id": f"c{i}", "visible": True} for i in range(61)]}
    assert client.put(URL, headers=h, json=too_many).status_code == 422
    assert client.put(URL, headers=h, json={"cards": [{"id": "x" * 65}]}).status_code == 422
    assert client.put(URL, headers=h, json={"cards": [{"id": "bad id!"}]}).status_code == 422
    assert client.put(URL, headers=h, json={"cards": [{"id": ""}]}).status_code == 422


def test_profile_patch_does_not_clear_layout():
    client = _make_client()
    h = _hdr(client, "dl4@x.com")
    body = {"version": 1, "cards": [{"id": "hero", "visible": False}]}
    client.put(URL, headers=h, json=body)
    assert client.patch("/api/v1/profile/me", headers=h, json={"display_name": "Bee"}).status_code == 200
    assert client.get(URL, headers=h).json()["layout"] == body


def test_requires_auth():
    client = _make_client()
    assert client.get(URL).status_code in (401, 403)
