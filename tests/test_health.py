from fastapi.testclient import TestClient

from src.main import app


def test_healthz() -> None:
    client = TestClient(app)
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_ready() -> None:
    client = TestClient(app)
    res = client.get("/ready")
    assert res.status_code == 200
    assert res.json()["status"] == "ready"
