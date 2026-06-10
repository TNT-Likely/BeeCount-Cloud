"""profile.primary_currency 往返契约:PATCH 大小写归一/部分更新不清空/非法值 422。"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


def _make_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app), TS


def _login(client, email):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email, "password": "Pa$$word1!", "device_id": "d1",
            "client_type": "app", "device_name": "pytest", "platform": "test",
        },
    )
    return r.json()["access_token"]


def test_primary_currency_roundtrip_and_normalize():
    client, _ = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_login(client, 'pc1@t.com')}"}
        r = client.patch("/api/v1/profile/me", headers=hdr, json={"primary_currency": "usd"})
        assert r.status_code == 200, r.text
        assert r.json()["primary_currency"] == "USD"
        assert client.get("/api/v1/profile/me", headers=hdr).json()["primary_currency"] == "USD"
    finally:
        app.dependency_overrides.clear()


def test_primary_currency_partial_update_keeps_value():
    client, _ = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_login(client, 'pc2@t.com')}"}
        client.patch("/api/v1/profile/me", headers=hdr, json={"primary_currency": "JPY"})
        client.patch("/api/v1/profile/me", headers=hdr, json={"display_name": "nick"})
        body = client.get("/api/v1/profile/me", headers=hdr).json()
        assert body["primary_currency"] == "JPY" and body["display_name"] == "nick"
    finally:
        app.dependency_overrides.clear()


def test_primary_currency_invalid_422():
    client, _ = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_login(client, 'pc3@t.com')}"}
        r = client.patch("/api/v1/profile/me", headers=hdr, json={"primary_currency": "US-1"})
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()
