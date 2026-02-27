#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import quantiles

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    try:
        return quantiles(values, n=100, method="inclusive")[94]
    except Exception:
        return max(values)


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


def _register_and_login(client: TestClient, email: str, password: str, client_type: str) -> dict:
    register = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "client_type": client_type,
            "device_name": f"nightly-{client_type}",
            "platform": client_type,
        },
    )
    if register.status_code != 200:
        raise RuntimeError(f"register failed: {register.status_code} {register.text}")
    return register.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Nightly performance smoke for web write/pull path")
    parser.add_argument("--dataset-size", type=int, default=1000)
    parser.add_argument("--read-samples", type=int, default=100)
    parser.add_argument("--output", type=str, default="artifacts/nightly-perf.json")
    args = parser.parse_args()

    client = _make_client()
    try:
        owner_app = _register_and_login(client, "nightly-owner@example.com", "123456", "app")
        owner_app_token = owner_app["access_token"]
        owner_device = owner_app["device_id"]

        init_snapshot = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {owner_app_token}"},
            json={
                "device_id": owner_device,
                "changes": [
                    {
                        "ledger_id": "nightly-ledger",
                        "entity_type": "ledger_snapshot",
                        "entity_sync_id": "nightly-ledger",
                        "action": "upsert",
                        "payload": {
                            "content": (
                                '{"ledgerName":"Nightly Ledger","currency":"CNY","count":0,'
                                '"items":[],"accounts":[],"categories":[],"tags":[]}'
                            )
                        },
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            },
        )
        if init_snapshot.status_code != 200:
            raise RuntimeError(f"init snapshot failed: {init_snapshot.status_code} {init_snapshot.text}")

        owner_web = client.post(
            "/api/v1/auth/login",
            json={
                "email": "nightly-owner@example.com",
                "password": "123456",
                "client_type": "web",
                "device_name": "nightly-web",
                "platform": "web",
            },
        )
        if owner_web.status_code != 200:
            raise RuntimeError(f"web login failed: {owner_web.status_code} {owner_web.text}")
        owner_web_token = owner_web.json()["access_token"]

        detail = client.get(
            "/api/v1/read/ledgers/nightly-ledger",
            headers={"Authorization": f"Bearer {owner_web_token}"},
        )
        if detail.status_code != 200:
            raise RuntimeError(f"detail failed: {detail.status_code} {detail.text}")
        base_change_id = int(detail.json()["source_change_id"])

        write_latencies_ms: list[float] = []
        read_latencies_ms: list[float] = []
        write_success = 0
        write_conflict = 0

        for i in range(max(args.dataset_size, 0)):
            started = time.perf_counter()
            res = client.post(
                "/api/v1/write/ledgers/nightly-ledger/transactions",
                headers={"Authorization": f"Bearer {owner_web_token}"},
                json={
                    "base_change_id": base_change_id,
                    "tx_type": "expense",
                    "amount": float((i % 100) + 1),
                    "happened_at": datetime.now(timezone.utc).isoformat(),
                    "note": f"nightly-{i}",
                },
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            write_latencies_ms.append(elapsed_ms)
            if res.status_code == 200:
                write_success += 1
                base_change_id = int(res.json()["new_change_id"])
            elif res.status_code == 409:
                write_conflict += 1
                latest_change_id = res.json().get("latest_change_id")
                if isinstance(latest_change_id, int):
                    base_change_id = latest_change_id
            else:
                raise RuntimeError(f"write failed: {res.status_code} {res.text}")

        for _ in range(max(args.read_samples, 0)):
            started = time.perf_counter()
            res = client.get(
                "/api/v1/read/ledgers/nightly-ledger/transactions?limit=200",
                headers={"Authorization": f"Bearer {owner_web_token}"},
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            read_latencies_ms.append(elapsed_ms)
            if res.status_code != 200:
                raise RuntimeError(f"read failed: {res.status_code} {res.text}")

        total_writes = max(args.dataset_size, 0)
        result = {
            "dataset_size": total_writes,
            "read_samples": max(args.read_samples, 0),
            "write_success_count": write_success,
            "write_conflict_count": write_conflict,
            "write_success_rate": (write_success / total_writes) if total_writes else 1.0,
            "write_conflict_rate": (write_conflict / total_writes) if total_writes else 0.0,
            "write_p95_ms": round(_p95(write_latencies_ms), 3),
            "read_p95_ms": round(_p95(read_latencies_ms), 3),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    main()
