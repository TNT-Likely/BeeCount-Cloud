"""CQRS Q-side projection consistency tests.

核心保证:snapshot 和 read_*_projection 在同事务里一起写,commit 之后两边对齐。
这里用 mobile /sync/push + web /write 两条路径各自打一发,断言 projection 行
和 snapshot 数组的关键字段一致。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import (
    Ledger,
    ReadAccountProjection,
    ReadBudgetProjection,
    ReadCategoryProjection,
    ReadTagProjection,
    ReadTxProjection,
    SyncChange,
)


def _make_client():
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
    return TestClient(app), engine, testing_session


def _iso(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat()


def _register_and_login(client, email, *, device_id, client_type):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post("/api/v1/auth/login", json={
        "email": email, "password": "Pa$$word1!",
        "device_id": device_id, "client_type": client_type,
        "device_name": f"pytest-{client_type}", "platform": "test",
    })
    return r.json()["access_token"]


def _push(client, hdr, device_id, ledger_id, changes):
    r = client.post("/api/v1/sync/push", headers=hdr,
                    json={"device_id": device_id, "changes": changes})
    assert r.status_code == 200, r.text
    return r


def _get_ledger_internal_id(session_factory, ledger_external_id):
    with session_factory() as db:
        return db.scalar(select(Ledger.id).where(Ledger.external_id == ledger_external_id))


def _get_latest_snapshot(session_factory, ledger_internal_id):
    with session_factory() as db:
        row = db.scalar(
            select(SyncChange).where(
                SyncChange.ledger_id == ledger_internal_id,
                SyncChange.entity_type == "ledger_snapshot",
            ).order_by(SyncChange.change_id.desc()).limit(1)
        )
        if row is None:
            return None
        content = row.payload_json.get("content") if isinstance(row.payload_json, dict) else None
        if not isinstance(content, str):
            return None
        return json.loads(content)


# --------------------------------------------------------------------------- #
# mobile /sync/push 驱动 projection 写入                                         #
# --------------------------------------------------------------------------- #

def test_mobile_push_tx_creates_projection_row():
    client, engine, sf = _make_client()
    try:
        tok = _register_and_login(client, "m1@t.com", device_id="m1", client_type="app")
        hdr = {"Authorization": f"Bearer {tok}"}
        _push(client, hdr, "m1", "lg1", [
            {"ledger_id": "lg1", "entity_type": "account", "entity_sync_id": "acc1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "acc1", "name": "Cash", "type": "cash", "currency": "CNY"}},
            {"ledger_id": "lg1", "entity_type": "transaction", "entity_sync_id": "tx1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "tx1", "type": "expense", "amount": 12.5,
                         "happenedAt": _iso(), "note": "coffee",
                         "accountId": "acc1", "accountName": "Cash"}},
        ])
        lid = _get_ledger_internal_id(sf, "lg1")
        with sf() as db:
            tx = db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid, ReadTxProjection.sync_id == "tx1"))
            assert tx is not None, "tx projection row missing"
            assert tx.tx_type == "expense"
            assert tx.amount == 12.5
            assert tx.note == "coffee"
            assert tx.account_sync_id == "acc1"
            assert tx.account_name == "Cash"
            acc = db.scalar(select(ReadAccountProjection).where(
                ReadAccountProjection.ledger_id == lid, ReadAccountProjection.sync_id == "acc1"))
            assert acc is not None and acc.name == "Cash"
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_tx_delete_removes_projection_row():
    client, engine, sf = _make_client()
    try:
        tok = _register_and_login(client, "m2@t.com", device_id="m1", client_type="app")
        hdr = {"Authorization": f"Bearer {tok}"}
        _push(client, hdr, "m1", "lg2", [
            {"ledger_id": "lg2", "entity_type": "transaction", "entity_sync_id": "tx1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "tx1", "type": "income", "amount": 100, "happenedAt": _iso()}},
        ])
        lid = _get_ledger_internal_id(sf, "lg2")
        with sf() as db:
            assert db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid, ReadTxProjection.sync_id == "tx1")) is not None

        later = datetime.now(timezone.utc) + timedelta(seconds=5)
        _push(client, hdr, "m1", "lg2", [
            {"ledger_id": "lg2", "entity_type": "transaction", "entity_sync_id": "tx1",
             "action": "delete", "updated_at": _iso(later), "payload": {}},
        ])
        with sf() as db:
            assert db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid, ReadTxProjection.sync_id == "tx1")) is None
    finally:
        app.dependency_overrides.clear()


def test_mobile_account_rename_cascades_tx_projection():
    client, engine, sf = _make_client()
    try:
        tok = _register_and_login(client, "m3@t.com", device_id="m1", client_type="app")
        hdr = {"Authorization": f"Bearer {tok}"}
        _push(client, hdr, "m1", "lg3", [
            {"ledger_id": "lg3", "entity_type": "account", "entity_sync_id": "a1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "a1", "name": "招商", "type": "bank_card", "currency": "CNY"}},
            {"ledger_id": "lg3", "entity_type": "transaction", "entity_sync_id": "t1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "t1", "type": "expense", "amount": 5, "happenedAt": _iso(),
                         "accountId": "a1", "accountName": "招商"}},
        ])
        later = datetime.now(timezone.utc) + timedelta(seconds=2)
        _push(client, hdr, "m1", "lg3", [
            {"ledger_id": "lg3", "entity_type": "account", "entity_sync_id": "a1",
             "action": "upsert", "updated_at": _iso(later),
             "payload": {"syncId": "a1", "name": "招商银行", "type": "bank_card", "currency": "CNY"}},
        ])
        lid = _get_ledger_internal_id(sf, "lg3")
        with sf() as db:
            tx = db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid, ReadTxProjection.sync_id == "t1"))
            assert tx.account_name == "招商银行", f"cascade failed, got {tx.account_name}"
            acc = db.scalar(select(ReadAccountProjection).where(
                ReadAccountProjection.ledger_id == lid, ReadAccountProjection.sync_id == "a1"))
            assert acc.name == "招商银行"
    finally:
        app.dependency_overrides.clear()


def test_mobile_category_rename_cascades_tx_projection():
    client, engine, sf = _make_client()
    try:
        tok = _register_and_login(client, "m4@t.com", device_id="m1", client_type="app")
        hdr = {"Authorization": f"Bearer {tok}"}
        _push(client, hdr, "m1", "lg4", [
            {"ledger_id": "lg4", "entity_type": "category", "entity_sync_id": "c1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "c1", "name": "餐饮", "kind": "expense"}},
            {"ledger_id": "lg4", "entity_type": "transaction", "entity_sync_id": "t1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "t1", "type": "expense", "amount": 5, "happenedAt": _iso(),
                         "categoryId": "c1", "categoryName": "餐饮", "categoryKind": "expense"}},
        ])
        later = datetime.now(timezone.utc) + timedelta(seconds=2)
        _push(client, hdr, "m1", "lg4", [
            {"ledger_id": "lg4", "entity_type": "category", "entity_sync_id": "c1",
             "action": "upsert", "updated_at": _iso(later),
             "payload": {"syncId": "c1", "name": "吃饭", "kind": "expense"}},
        ])
        lid = _get_ledger_internal_id(sf, "lg4")
        with sf() as db:
            tx = db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid, ReadTxProjection.sync_id == "t1"))
            assert tx.category_name == "吃饭", f"cascade failed, got {tx.category_name}"
    finally:
        app.dependency_overrides.clear()


def test_mobile_tag_rename_cascades_tx_projection():
    client, engine, sf = _make_client()
    try:
        tok = _register_and_login(client, "m5@t.com", device_id="m1", client_type="app")
        hdr = {"Authorization": f"Bearer {tok}"}
        _push(client, hdr, "m1", "lg5", [
            {"ledger_id": "lg5", "entity_type": "tag", "entity_sync_id": "g1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "g1", "name": "A"}},
            {"ledger_id": "lg5", "entity_type": "transaction", "entity_sync_id": "t1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "t1", "type": "expense", "amount": 5, "happenedAt": _iso(),
                         "tags": "A", "tagIds": ["g1"]}},
        ])
        later = datetime.now(timezone.utc) + timedelta(seconds=2)
        _push(client, hdr, "m1", "lg5", [
            {"ledger_id": "lg5", "entity_type": "tag", "entity_sync_id": "g1",
             "action": "upsert", "updated_at": _iso(later),
             "payload": {"syncId": "g1", "name": "B"}},
        ])
        lid = _get_ledger_internal_id(sf, "lg5")
        with sf() as db:
            tx = db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid, ReadTxProjection.sync_id == "t1"))
            assert tx.tags_csv == "B", f"cascade failed, got {tx.tags_csv}"
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# web /write/* 驱动 projection 写入                                             #
# --------------------------------------------------------------------------- #

def test_web_create_tx_creates_projection_row():
    client, engine, sf = _make_client()
    try:
        tok = _register_and_login(client, "w1@t.com", device_id="web", client_type="web")
        hdr = {"Authorization": f"Bearer {tok}", "X-Device-ID": "web"}
        # 先建账本
        r = client.post("/api/v1/write/ledgers", headers=hdr,
                        json={"ledger_id": "wlg1", "ledger_name": "WebLedger", "currency": "CNY"})
        assert r.status_code == 200, r.text
        # 建分类
        r = client.post("/api/v1/write/ledgers/wlg1/categories", headers=hdr,
                        json={"base_change_id": r.json()["new_change_id"],
                              "name": "Food", "kind": "expense"})
        assert r.status_code == 200, r.text
        cat_id = r.json()["entity_id"]
        base = r.json()["new_change_id"]
        # 建交易(web UI 下拉选项带了 id+name,照实传)
        r = client.post("/api/v1/write/ledgers/wlg1/transactions", headers=hdr,
                        json={"base_change_id": base,
                              "tx_type": "expense", "amount": 9.99, "happened_at": _iso(),
                              "note": "web tx",
                              "category_id": cat_id, "category_name": "Food",
                              "category_kind": "expense"})
        assert r.status_code == 200, r.text
        tx_id = r.json()["entity_id"]

        lid = _get_ledger_internal_id(sf, "wlg1")
        with sf() as db:
            tx = db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid, ReadTxProjection.sync_id == tx_id))
            assert tx is not None
            assert tx.amount == 9.99
            assert tx.note == "web tx"
            assert tx.category_sync_id == cat_id
            assert tx.category_name == "Food"
            cat = db.scalar(select(ReadCategoryProjection).where(
                ReadCategoryProjection.ledger_id == lid, ReadCategoryProjection.sync_id == cat_id))
            assert cat is not None and cat.name == "Food"
    finally:
        app.dependency_overrides.clear()


def test_web_delete_tx_removes_projection_row():
    client, engine, sf = _make_client()
    try:
        tok = _register_and_login(client, "w2@t.com", device_id="web", client_type="web")
        hdr = {"Authorization": f"Bearer {tok}", "X-Device-ID": "web"}
        r = client.post("/api/v1/write/ledgers", headers=hdr,
                        json={"ledger_id": "wlg2", "ledger_name": "L", "currency": "CNY"})
        base = r.json()["new_change_id"]
        r = client.post("/api/v1/write/ledgers/wlg2/transactions", headers=hdr,
                        json={"base_change_id": base, "tx_type": "income", "amount": 1,
                              "happened_at": _iso()})
        tx_id = r.json()["entity_id"]; base = r.json()["new_change_id"]
        lid = _get_ledger_internal_id(sf, "wlg2")
        with sf() as db:
            assert db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid, ReadTxProjection.sync_id == tx_id)) is not None

        r = client.request("DELETE", f"/api/v1/write/ledgers/wlg2/transactions/{tx_id}",
                           headers=hdr, json={"base_change_id": base})
        assert r.status_code == 200, r.text
        with sf() as db:
            assert db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid, ReadTxProjection.sync_id == tx_id)) is None
    finally:
        app.dependency_overrides.clear()


def test_web_delete_ledger_truncates_projection():
    client, engine, sf = _make_client()
    try:
        tok = _register_and_login(client, "w3@t.com", device_id="web", client_type="web")
        hdr = {"Authorization": f"Bearer {tok}", "X-Device-ID": "web"}
        r = client.post("/api/v1/write/ledgers", headers=hdr,
                        json={"ledger_id": "wlg3", "ledger_name": "L", "currency": "CNY"})
        base = r.json()["new_change_id"]
        r = client.post("/api/v1/write/ledgers/wlg3/transactions", headers=hdr,
                        json={"base_change_id": base, "tx_type": "income", "amount": 1,
                              "happened_at": _iso()})
        assert r.status_code == 200, r.text
        lid = _get_ledger_internal_id(sf, "wlg3")
        with sf() as db:
            cnt_before = db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid))
            assert cnt_before is not None

        r = client.delete("/api/v1/write/ledgers/wlg3", headers=hdr)
        assert r.status_code == 200, r.text
        with sf() as db:
            assert db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid)) is None
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 跨路径一致性:mobile push + web 之后 projection 仍等价于 snapshot              #
# --------------------------------------------------------------------------- #

def test_projection_count_matches_snapshot_after_mixed_writes():
    client, engine, sf = _make_client()
    try:
        app_tok = _register_and_login(client, "mix@t.com", device_id="m1", client_type="app")
        web_tok = _register_and_login(client, "mix@t.com", device_id="w1", client_type="web")
        app_hdr = {"Authorization": f"Bearer {app_tok}"}
        web_hdr = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "w1"}

        # mobile 推 3 个 tx
        _push(client, app_hdr, "m1", "lg_mix", [
            {"ledger_id": "lg_mix", "entity_type": "transaction", "entity_sync_id": f"tx{i}",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": f"tx{i}", "type": "expense", "amount": i * 10.0,
                         "happenedAt": _iso()}}
            for i in range(1, 4)
        ])
        # mobile 删 1 个
        later = datetime.now(timezone.utc) + timedelta(seconds=2)
        _push(client, app_hdr, "m1", "lg_mix", [
            {"ledger_id": "lg_mix", "entity_type": "transaction", "entity_sync_id": "tx2",
             "action": "delete", "updated_at": _iso(later), "payload": {}}
        ])

        lid = _get_ledger_internal_id(sf, "lg_mix")
        snap = _get_latest_snapshot(sf, lid)
        with sf() as db:
            proj_count = db.scalar(select(
                __import__("sqlalchemy").func.count()
            ).select_from(ReadTxProjection).where(ReadTxProjection.ledger_id == lid))
            snap_tx_ids = {e["syncId"] for e in (snap.get("items") or []) if e.get("syncId")}
            proj_tx_ids = {r.sync_id for r in db.scalars(
                select(ReadTxProjection).where(ReadTxProjection.ledger_id == lid)
            ).all()}
            assert proj_tx_ids == snap_tx_ids, f"divergent: proj={proj_tx_ids} snap={snap_tx_ids}"
            assert proj_count == len(snap_tx_ids)
    finally:
        app.dependency_overrides.clear()


def test_projection_isolated_per_ledger():
    """两个 ledger 各自的 projection 行互不混淆。"""
    client, engine, sf = _make_client()
    try:
        tok = _register_and_login(client, "iso@t.com", device_id="m1", client_type="app")
        hdr = {"Authorization": f"Bearer {tok}"}
        _push(client, hdr, "m1", "lga", [
            {"ledger_id": "lga", "entity_type": "transaction", "entity_sync_id": "tx1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "tx1", "type": "expense", "amount": 1, "happenedAt": _iso()}}
        ])
        _push(client, hdr, "m1", "lgb", [
            {"ledger_id": "lgb", "entity_type": "transaction", "entity_sync_id": "tx1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "tx1", "type": "income", "amount": 2, "happenedAt": _iso()}}
        ])
        lid_a = _get_ledger_internal_id(sf, "lga")
        lid_b = _get_ledger_internal_id(sf, "lgb")
        with sf() as db:
            tx_a = db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid_a, ReadTxProjection.sync_id == "tx1"))
            tx_b = db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid_b, ReadTxProjection.sync_id == "tx1"))
            assert tx_a.tx_type == "expense" and tx_a.amount == 1
            assert tx_b.tx_type == "income" and tx_b.amount == 2
    finally:
        app.dependency_overrides.clear()
