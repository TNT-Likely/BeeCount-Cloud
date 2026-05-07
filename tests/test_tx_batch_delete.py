"""POST /write/ledgers/{lid}/transactions/batch/delete + CSV export by tx_ids。

用 batch_create 先建一批 tx,然后:
1. happy path:全删 → projection 行被标 deleted,sync_change 类型 = delete
2. 部分失败:混伪造 ID → response.failed 含 not_found,真实 ID 正常删
3. 跨 ledger 防越权:跨 ledger sync_id 报 not_found(因为不在 snapshot)
4. 上限校验:201 条 → 422
5. idempotency:同 key 重发 → replay 第一份响应
6. CSV by tx_ids:多选导出走 sync_id IN
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadTxProjection, SyncChange


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


def _create_ledger(client: TestClient, token: str, name: str = "default") -> str:
    r = client.post(
        "/api/v1/write/ledgers",
        json={"ledger_name": name, "currency": "CNY"},
        headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
    )
    assert r.status_code == 200, r.text
    return r.json()["entity_id"]


def _seed_txs(client: TestClient, token: str, ledger_id: str, n: int) -> list[str]:
    """通过 batch create 一次造 n 笔交易,返回 sync_ids。"""
    r = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions/batch",
        json={
            "base_change_id": 0,
            "transactions": [
                {
                    "tx_type": "expense",
                    "amount": 10.0 + i,
                    "happened_at": "2026-05-06T12:30:00Z",
                    "note": f"item {i}",
                    "tags": [],
                }
                for i in range(n)
            ],
            "auto_ai_tag": False,
            "locale": "zh",
        },
        headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
    )
    assert r.status_code == 200, r.text
    return r.json()["created_sync_ids"]


# ──────────────────────────────────────────────────────────────────────


def test_batch_delete_happy_path():
    client = _make_client()
    try:
        token = _register_and_login(client, "bd1@test.com")
        ledger_id = _create_ledger(client, token)
        tx_ids = _seed_txs(client, token, ledger_id, n=3)

        r = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/batch/delete",
            json={"tx_ids": tx_ids, "base_change_id": 0},
            headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert sorted(body["deleted_tx_ids"]) == sorted(tx_ids)
        assert body["failed"] == []
        assert body["new_change_id"] > 0

        # projection 行应该不在了(delete_transaction 走 _emit_entity_diffs 处理)
        db = next(app.dependency_overrides[get_db]())
        try:
            remaining = db.scalars(
                select(ReadTxProjection).where(ReadTxProjection.sync_id.in_(tx_ids))
            ).all()
            assert remaining == []
            # 验证 sync_change 里有 delete 类型记录
            del_changes = db.scalars(
                select(SyncChange).where(
                    SyncChange.entity_type == "transaction",
                    SyncChange.action == "delete",
                )
            ).all()
            assert len(del_changes) == 3
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_batch_delete_partial_failure_with_fake_ids():
    client = _make_client()
    try:
        token = _register_and_login(client, "bd2@test.com")
        ledger_id = _create_ledger(client, token)
        real_ids = _seed_txs(client, token, ledger_id, n=2)
        fake_ids = ["tx_does_not_exist_1", "tx_does_not_exist_2"]

        r = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/batch/delete",
            json={"tx_ids": real_ids + fake_ids, "base_change_id": 0},
            headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert sorted(body["deleted_tx_ids"]) == sorted(real_ids)
        assert len(body["failed"]) == 2
        for f in body["failed"]:
            assert f["reason"] == "not_found"
            assert f["tx_id"] in fake_ids
    finally:
        app.dependency_overrides.clear()


def test_batch_delete_cross_ledger_returns_not_found():
    """从 ledger A 提交 ledger B 的 tx_id —— 不在 A 的 snapshot 里 → not_found。"""
    client = _make_client()
    try:
        token = _register_and_login(client, "bd3@test.com")
        ledger_a = _create_ledger(client, token, name="A")
        ledger_b = _create_ledger(client, token, name="B")
        ids_in_b = _seed_txs(client, token, ledger_b, n=2)

        # 用 ledger_a 的路径提交 b 的 ids
        r = client.post(
            f"/api/v1/write/ledgers/{ledger_a}/transactions/batch/delete",
            json={"tx_ids": ids_in_b, "base_change_id": 0},
            headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["deleted_tx_ids"] == []
        assert len(body["failed"]) == 2
        # ledger_b 里的 tx 应该完好
        db = next(app.dependency_overrides[get_db]())
        try:
            remaining = db.scalars(
                select(ReadTxProjection).where(ReadTxProjection.sync_id.in_(ids_in_b))
            ).all()
            assert len(remaining) == 2
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_batch_delete_max_size_limit():
    client = _make_client()
    try:
        token = _register_and_login(client, "bd4@test.com")
        ledger_id = _create_ledger(client, token)
        # 201 条伪造 id —— 在 pydantic 校验阶段就该 422
        too_many = [f"tx_{i}" for i in range(201)]
        r = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/batch/delete",
            json={"tx_ids": too_many, "base_change_id": 0},
            headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
        )
        assert r.status_code == 422, r.text
    finally:
        app.dependency_overrides.clear()


def test_batch_delete_idempotency_replay():
    client = _make_client()
    try:
        token = _register_and_login(client, "bd5@test.com")
        ledger_id = _create_ledger(client, token)
        tx_ids = _seed_txs(client, token, ledger_id, n=2)
        idem_key = "test-idem-1"

        headers = {
            "Authorization": f"Bearer {token}",
            "X-Device-ID": "d-web",
            "Idempotency-Key": idem_key,
        }
        r1 = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/batch/delete",
            json={"tx_ids": tx_ids, "base_change_id": 0},
            headers=headers,
        )
        assert r1.status_code == 200, r1.text
        first_change_id = r1.json()["new_change_id"]

        # 重发同 key → replay 第一份;不能再次推进 change_id
        r2 = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/batch/delete",
            json={"tx_ids": tx_ids, "base_change_id": 0},
            headers=headers,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["new_change_id"] == first_change_id
        assert sorted(r2.json()["deleted_tx_ids"]) == sorted(tx_ids)
    finally:
        app.dependency_overrides.clear()


# ──────────────────────────────────────────────────────────────────────
# CSV export by tx_ids
# ──────────────────────────────────────────────────────────────────────


def test_csv_export_by_tx_ids():
    client = _make_client()
    try:
        token = _register_and_login(client, "csv1@test.com")
        ledger_id = _create_ledger(client, token)
        all_ids = _seed_txs(client, token, ledger_id, n=5)
        # 只导出前 2 条
        selected = all_ids[:2]

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params=[("ledger_id", ledger_id), *[("tx_ids", t) for t in selected]],
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.text
        # 1 行表头 + 2 行数据(BOM 在第一行 yield)
        lines = [l for l in body.splitlines() if l.strip()]
        assert len(lines) == 3, body
    finally:
        app.dependency_overrides.clear()


def test_csv_export_by_empty_tx_ids_returns_only_header():
    """tx_ids 全是伪造 → 只有表头,没有数据行。"""
    client = _make_client()
    try:
        token = _register_and_login(client, "csv2@test.com")
        ledger_id = _create_ledger(client, token)
        _seed_txs(client, token, ledger_id, n=3)  # 真的有 tx,但不查它们

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params=[
                ("ledger_id", ledger_id),
                ("tx_ids", "tx_does_not_exist"),
            ],
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        lines = [l for l in r.text.splitlines() if l.strip()]
        assert len(lines) == 1  # 只有表头
    finally:
        app.dependency_overrides.clear()


def test_csv_export_tx_ids_ignores_other_filters():
    """传 tx_ids 时其它 filter(date_from / q)应被忽略。"""
    client = _make_client()
    try:
        token = _register_and_login(client, "csv3@test.com")
        ledger_id = _create_ledger(client, token)
        ids = _seed_txs(client, token, ledger_id, n=3)

        # 故意传一个跟 tx 不 match 的 q;按设计 tx_ids 优先 → 仍导出全 3 条
        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params=[
                ("ledger_id", ledger_id),
                ("q", "DOES_NOT_MATCH_ANYTHING_xyz"),
                *[("tx_ids", t) for t in ids],
            ],
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        lines = [l for l in r.text.splitlines() if l.strip()]
        assert len(lines) == 4, r.text  # 表头 + 3 条
    finally:
        app.dependency_overrides.clear()
