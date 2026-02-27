from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


def _make_client() -> TestClient:
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
    return TestClient(app)


def test_login_validation_error_returns_422() -> None:
    client = _make_client()
    try:
        res = client.post(
            "/api/v1/auth/login",
            json={
                "email": "",
                "password": "123456",
                "client_type": "web",
                "device_name": "web",
                "platform": "web",
            },
        )
        assert res.status_code == 422
        body = res.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["detail"] == "Request validation failed"
    finally:
        app.dependency_overrides.clear()


def test_refresh_rotates_token_without_unique_conflict() -> None:
    client = _make_client()
    try:
        register = client.post(
            "/api/v1/auth/register",
            json={
                "email": "refresh@example.com",
                "password": "123456",
                "client_type": "web",
                "device_name": "web",
                "platform": "web",
            },
        )
        assert register.status_code == 200
        first_refresh = register.json()["refresh_token"]

        refreshed = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": first_refresh},
        )
        assert refreshed.status_code == 200
        second_refresh = refreshed.json()["refresh_token"]
        assert second_refresh != first_refresh

        refreshed_again = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": second_refresh},
        )
        assert refreshed_again.status_code == 200
    finally:
        app.dependency_overrides.clear()
