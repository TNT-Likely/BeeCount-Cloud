"""CQRS Q-side projection consistency tests.

核心保证:snapshot 和 read_*_projection 在同事务里一起写,commit 之后两边对齐。
这里用 mobile /sync/push + web /write 两条路径各自打一发,断言 projection 行
和 snapshot 数组的关键字段一致。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import snapshot_builder
from src.database import Base, get_db
from src.main import app
from src.models import (
    Ledger,
    ReadBudgetProjection,
    ReadTxProjection,
    SyncChange,
    UserAccountProjection,
    UserCategoryProjection,
    UserTagProjection,
)
from src.snapshot_mutator import create_transaction


@pytest.mark.parametrize("projected_time", [
    "2026-11-23T22:30:00.123456",        # SQLite UTC without tzinfo
    "2026-11-24T06:30:00.123456+08:00",  # PostgreSQL Asia/Shanghai
    "2026-11-23T17:30:00.123456-05:00",  # PostgreSQL America/New_York
])
def test_web_batch_preserves_existing_projection_timestamp(projected_time):
    old = {"syncId": "existing", "type": "expense", "amount": 1.0,
           "happenedAt": snapshot_builder._to_iso_utc(datetime.fromisoformat(projected_time))}
    snapshot, _ = create_transaction({"items": [old]}, {
        "tx_type": "expense", "amount": 2, "happened_at": "2026-11-24T00:00:00Z",
    })

    # The batch diff compares these dictionaries to decide which rows to emit.
    assert snapshot["items"][0] == old
    assert old["happenedAt"] == "2026-11-23T22:30:00.123456+00:00"


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


def _get_ledger_user_id(session_factory, ledger_external_id):
    """user-global 资源按 user_id 查,helper 把 external_id → owner user_id。"""
    with session_factory() as db:
        return db.scalar(select(Ledger.user_id).where(Ledger.external_id == ledger_external_id))


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
            uid = _get_ledger_user_id(sf, "lg1")
            acc = db.scalar(select(UserAccountProjection).where(
                UserAccountProjection.user_id == uid, UserAccountProjection.sync_id == "acc1"))
            assert acc is not None and acc.name == "Cash"
    finally:
        app.dependency_overrides.clear()


def test_optional_transaction_fields_preserve_omissions_and_propagate_explicit_clears():
    client, _, sf = _make_client()
    try:
        email = "optional-tx-fields@qa.example.com"
        app_token = _register_and_login(client, email, device_id="phone", client_type="app")
        app_headers = {"Authorization": f"Bearer {app_token}"}
        web_login = client.post("/api/v1/auth/login", json={
            "email": email, "password": "Pa$$word1!", "device_id": "browser",
            "client_type": "web", "device_name": "web", "platform": "test",
        })
        web_headers = {"Authorization": f"Bearer {web_login.json()['access_token']}"}
        start = datetime.now(timezone.utc)

        def edit(step, **fields):
            _push(client, app_headers, "phone", "fields", [{
                "ledger_id": "fields", "entity_type": "transaction", "entity_sync_id": "bill",
                "action": "upsert", "updated_at": _iso(start + timedelta(seconds=step)),
                "payload": {"syncId": "bill", **fields},
            }])

        def values():
            with sf() as db:
                row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == "bill"))
                return row.note, row.tags_csv, json.loads(row.tag_sync_ids_json or "[]")

        edit(0, type="expense", amount=2, happenedAt=_iso(), note="original",
             tags="Original", tagIds=["tag-1"])
        edit(1, amount=3)
        assert values() == ("original", "Original", ["tag-1"])
        edit(2, tags="Only name")
        assert values() == ("original", "Only name", [])
        edit(3, tagIds=["tag-2"])
        assert values() == ("original", None, ["tag-2"])
        edit(4, note=None, tags=None)
        assert values() == (None, None, [])
        edit(5, note="replaced", tags="Replacement", tagIds=["tag-3"])
        edit(6, tagIds=[])
        assert values() == ("replaced", None, [])
        edit(7, tags="Restored", tagIds=["tag-4"])

        # Web PATCH produces a complete item. Its event must tell mobile that
        # both nullable fields were cleared, even if the snapshot omits them.
        response = client.patch("/api/v1/write/ledgers/fields/transactions/bill",
                                headers=web_headers,
                                json={"note": None, "tags": [], "base_change_id": 0})
        assert response.status_code == 200, response.text
        assert values() == (None, None, [])
        events = client.get("/api/v1/sync/pull?since=0", headers=app_headers).json()["changes"]
        payload = [event["payload"] for event in events if event["entity_sync_id"] == "bill"][-1]
        assert payload["note"] is None and payload["tags"] == "" and payload["tagIds"] == []
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_transaction_type_change_clears_stale_transfer_accounts():
    """切换交易类型时，partial sync 不能恢复旧的转出/转入账户关联。"""
    client, engine, sf = _make_client()
    try:
        tok = _register_and_login(client, "tx-type-switch@t.com", device_id="tx-switch", client_type="app")
        hdr = {"Authorization": f"Bearer {tok}"}
        _push(client, hdr, "tx-switch", "lg1", [
            {
                "ledger_id": "lg1", "entity_type": "transaction", "entity_sync_id": "t-switch",
                "action": "upsert", "updated_at": _iso(),
                "payload": {
                    "syncId": "t-switch", "type": "transfer", "amount": 5.25,
                    "happenedAt": _iso(),
                    "fromAccountId": "a-rich", "fromAccountName": "活期富",
                    "toAccountId": "a-salary", "toAccountName": "工资卡",
                },
            },
        ])
        _push(client, hdr, "tx-switch", "lg1", [
            {
                "ledger_id": "lg1", "entity_type": "transaction", "entity_sync_id": "t-switch",
                "action": "upsert", "updated_at": _iso(),
                "payload": {
                    "syncId": "t-switch", "type": "expense", "amount": 5.25,
                    "happenedAt": _iso(),
                    "accountId": "a-salary", "accountName": "工资卡",
                },
            },
        ])
        lid = _get_ledger_internal_id(sf, "lg1")
        with sf() as db:
            tx = db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid, ReadTxProjection.sync_id == "t-switch"
            ))
            assert tx is not None
            assert tx.tx_type == "expense"
            assert tx.account_sync_id == "a-salary"
            assert tx.from_account_sync_id is None
            assert tx.from_account_name is None
            assert tx.to_account_sync_id is None
            assert tx.to_account_name is None
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
            uid = _get_ledger_user_id(sf, "lg3")
            acc = db.scalar(select(UserAccountProjection).where(
                UserAccountProjection.user_id == uid, UserAccountProjection.sync_id == "a1"))
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


def test_mobile_tag_rename_cascades_when_transaction_only_has_tag_name():
    """Issue #5 regression: legacy/mobile tx may only carry tags_csv without tagIds.
    Renaming the tag entity must still refresh existing transaction tag names.
    """
    client, engine, sf = _make_client()
    try:
        tok = _register_and_login(client, "m5-name-only@t.com", device_id="m1", client_type="app")
        hdr = {"Authorization": f"Bearer {tok}"}
        _push(client, hdr, "m1", "lg5_name_only", [
            {"ledger_id": "lg5_name_only", "entity_type": "tag", "entity_sync_id": "g1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "g1", "name": "旧标签"}},
            {"ledger_id": "lg5_name_only", "entity_type": "transaction", "entity_sync_id": "t1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "t1", "type": "expense", "amount": 5, "happenedAt": _iso(),
                         "tags": "旧标签"}},
        ])
        later = datetime.now(timezone.utc) + timedelta(seconds=2)
        _push(client, hdr, "m1", "lg5_name_only", [
            {"ledger_id": "lg5_name_only", "entity_type": "tag", "entity_sync_id": "g1",
             "action": "upsert", "updated_at": _iso(later),
             "payload": {"syncId": "g1", "name": "新标签"}},
        ])
        lid = _get_ledger_internal_id(sf, "lg5_name_only")
        with sf() as db:
            tx = db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid, ReadTxProjection.sync_id == "t1"))
            assert tx.tags_csv == "新标签", f"cascade failed, got {tx.tags_csv}"
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# web /write/* 驱动 projection 写入                                             #
# --------------------------------------------------------------------------- #


def test_mobile_partial_push_receipt_keeps_transaction_instant():
    client, _, _ = _make_client()
    try:
        token = _register_and_login(client, "receipt-time@qa.example.com",
                                    device_id="clock", client_type="app")
        headers = {"Authorization": f"Bearer {token}"}
        instant = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
        tx = {"ledger_id": "clock-ledger", "entity_type": "transaction",
              "entity_sync_id": "breakfast", "action": "upsert", "updated_at": _iso(),
              "payload": {"type": "expense", "amount": 28.5, "happenedAt": instant.isoformat()}}
        _push(client, headers, "clock", "clock-ledger", [tx])
        patch = {**tx, "updated_at": _iso(), "changed_fields": ["note"],
                 "operation_id": "receipt-note", "payload": {"note": "revised"}}
        response = client.post("/api/v1/sync/push", headers=headers, json={
            "device_id": "clock", "changes": [patch], "return_results": True})
        assert response.status_code == 200, response.text
        receipt = response.json()["results"][0]
        assert receipt["status"] == "accepted"
        assert datetime.fromisoformat(receipt["winner"]["payload"]["happenedAt"]) == instant
    finally:
        app.dependency_overrides.clear()


def test_mobile_tag_delete_removes_only_its_references_across_owner_ledgers():
    client, _, _ = _make_client()
    try:
        token = _register_and_login(client, "delete-tag@qa.example.com",
                                    device_id="tags", client_type="app")
        headers = {"Authorization": f"Bearer {token}"}

        def event(ledger, kind, key, payload, action="upsert"):
            return {"ledger_id": ledger, "entity_type": kind, "entity_sync_id": key,
                    "action": action, "updated_at": _iso(), "payload": payload}

        def tx(ledger, key, names, ids):
            payload = {"type": "expense", "amount": 28.5, "happenedAt": _iso(), "tags": names}
            if ids is not None:
                payload["tagIds"] = ids
            return event(ledger, "transaction", key, payload)

        _push(client, headers, "tags", "first", [
            event(None, "tag", "retire", {"name": "Travel"}),
            event(None, "tag", "keep", {"name": "TravelPlus"}),
            tx("first", "multiple", "Travel,TravelPlus", ["retire", "keep"]),
            tx("second", "last", "Travel", ["retire"]),
            tx("second", "legacy", "Travel,TravelPlus", None),
            tx("second", "unrelated", "TravelPlus", ["keep"]),
        ])
        other = {"Authorization": "Bearer " + _register_and_login(
            client, "other-tag-owner@qa.example.com", device_id="other", client_type="app")}
        _push(client, other, "other", "private", [
            event(None, "tag", "retire", {"name": "Travel"}),
            tx("private", "other-transaction", "Travel", ["retire"]),
        ])
        _push(client, headers, "tags", "first", [event(None, "tag", "retire", {}, "delete")])
        seen = {}
        for book in ("first", "second"):
            full = client.get("/api/v1/sync/full", headers=headers, params={"ledger_id": book}).json()
            content = json.loads(full["snapshot"]["payload"]["content"])
            assert {row["syncId"] for row in content["tags"]} == {"keep"}
            for row in content["items"]:
                seen[row["syncId"]] = (row.get("tags"), row.get("tagIds", []))
        assert seen == {"multiple": ("TravelPlus", ["keep"]), "last": (None, []),
                        "legacy": ("TravelPlus", []), "unrelated": ("TravelPlus", ["keep"])}
        full = client.get("/api/v1/sync/full", headers=other, params={"ledger_id": "private"}).json()
        foreign = json.loads(full["snapshot"]["payload"]["content"])
        assert foreign["items"][0]["tagIds"] == ["retire"] and foreign["tags"][0]["name"] == "Travel"
    finally:
        app.dependency_overrides.clear()


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
            uid = _get_ledger_user_id(sf, "wlg1")
            cat = db.scalar(select(UserCategoryProjection).where(
                UserCategoryProjection.user_id == uid, UserCategoryProjection.sync_id == cat_id))
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
        # 方案 B 后 snapshot 从 projection 懒构建,跟 projection 必然一致
        with sf() as db:
            ledger = db.scalar(select(Ledger).where(Ledger.id == lid))
            snap = snapshot_builder.build(db, ledger)
            proj_count = db.scalar(select(
                __import__("sqlalchemy").func.count()
            ).select_from(ReadTxProjection).where(ReadTxProjection.ledger_id == lid))
            built_tx_ids = {e["syncId"] for e in (snap.get("items") or []) if e.get("syncId")}
            proj_tx_ids = {r.sync_id for r in db.scalars(
                select(ReadTxProjection).where(ReadTxProjection.ledger_id == lid)
            ).all()}
            assert proj_tx_ids == built_tx_ids
            assert proj_tx_ids == {"tx1", "tx3"}, f"expected tx1+tx3 after tx2 delete, got {proj_tx_ids}"
            assert proj_count == 2
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


# --------------------------------------------------------------------------- #
# Budget coverage                                                              #
# --------------------------------------------------------------------------- #
# 2026-04 踩过坑:_merge_with_existing_budget 的 `from ..models import
# ReadBudgetProjection` 一度被写成 `from .models`,模块加载不报错,只有在
# 真推一条 budget change 时才会 ModuleNotFoundError → 500。现在 merge 逻辑
# 收敛到表驱动的 _merge_with_existing + 顶层统一 import,这种类型错误不会
# 再发生。下面这些 budget / 分类规范化 test 作为回归保底。


def test_mobile_push_budget_creates_projection_row():
    """纯创建:budget 能被 /sync/push 接受并写入 read_budget_projection。
    2026-04 之前这里直接 500(ModuleNotFoundError on `.models`)。"""
    client, engine, sf = _make_client()
    try:
        tok = _register_and_login(client, "b1@t.com", device_id="b1", client_type="app")
        hdr = {"Authorization": f"Bearer {tok}"}
        _push(client, hdr, "b1", "lg1", [
            {"ledger_id": "lg1", "entity_type": "budget", "entity_sync_id": "bud1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "bud1", "type": "total", "amount": 500.0,
                         "period": "monthly", "startDay": 1, "enabled": True}},
        ])
        lid = _get_ledger_internal_id(sf, "lg1")
        with sf() as db:
            b = db.scalar(select(ReadBudgetProjection).where(
                ReadBudgetProjection.ledger_id == lid, ReadBudgetProjection.sync_id == "bud1"))
            assert b is not None, "budget projection row missing"
            assert b.budget_type == "total"
            assert b.amount == 500.0
            assert b.period == "monthly"
            assert b.start_day == 1
            assert b.enabled is True
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_budget_partial_update_keeps_existing_fields():
    """增量 update:只推 amount,其它字段(period / startDay / enabled)
    必须保留旧值。如果 _merge_with_existing 读不到现有行就拿 None 覆盖旧值,
    这一条会失败。"""
    client, engine, sf = _make_client()
    try:
        tok = _register_and_login(client, "b2@t.com", device_id="b2", client_type="app")
        hdr = {"Authorization": f"Bearer {tok}"}
        # 先完整 create
        _push(client, hdr, "b2", "lg1", [
            {"ledger_id": "lg1", "entity_type": "budget", "entity_sync_id": "bud1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "bud1", "type": "category", "categoryId": "cat-x",
                         "amount": 300.0, "period": "monthly", "startDay": 5, "enabled": True}},
        ])
        # 再用只有 amount 的增量 update(mobile 只改了金额)
        _push(client, hdr, "b2", "lg1", [
            {"ledger_id": "lg1", "entity_type": "budget", "entity_sync_id": "bud1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "bud1", "amount": 700.0}},
        ])
        lid = _get_ledger_internal_id(sf, "lg1")
        with sf() as db:
            b = db.scalar(select(ReadBudgetProjection).where(
                ReadBudgetProjection.ledger_id == lid, ReadBudgetProjection.sync_id == "bud1"))
            assert b.amount == 700.0, "新 amount 没落"
            # 旧值必须保留 —— 这是 _merge_with_existing 的核心契约
            assert b.budget_type == "category"
            assert b.category_sync_id == "cat-x"
            assert b.period == "monthly"
            assert b.start_day == 5
            assert b.enabled is True
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_mixed_entities_in_one_batch():
    """一次 push 里混合五种 entity(account / category / tag / budget / transaction)。
    之前 budget 500 时整批 rollback,这个测试作为回归:五种都能走通。"""
    client, engine, sf = _make_client()
    try:
        tok = _register_and_login(client, "mix@t.com", device_id="mix", client_type="app")
        hdr = {"Authorization": f"Bearer {tok}"}
        _push(client, hdr, "mix", "lg1", [
            {"ledger_id": "lg1", "entity_type": "account", "entity_sync_id": "a1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "a1", "name": "Cash", "type": "cash", "currency": "CNY"}},
            {"ledger_id": "lg1", "entity_type": "category", "entity_sync_id": "c1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "c1", "name": "Food", "kind": "expense"}},
            {"ledger_id": "lg1", "entity_type": "tag", "entity_sync_id": "tg1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "tg1", "name": "work", "color": "#F00"}},
            {"ledger_id": "lg1", "entity_type": "budget", "entity_sync_id": "bu1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "bu1", "type": "total", "amount": 1000,
                         "period": "monthly", "startDay": 1, "enabled": True}},
            {"ledger_id": "lg1", "entity_type": "transaction", "entity_sync_id": "t1",
             "action": "upsert", "updated_at": _iso(),
             "payload": {"syncId": "t1", "type": "expense", "amount": 12,
                         "happenedAt": _iso(), "accountId": "a1", "accountName": "Cash"}},
        ])
        lid = _get_ledger_internal_id(sf, "lg1")
        uid = _get_ledger_user_id(sf, "lg1")
        with sf() as db:
            assert db.scalar(select(UserAccountProjection).where(
                UserAccountProjection.user_id == uid, UserAccountProjection.sync_id == "a1"))
            assert db.scalar(select(UserCategoryProjection).where(
                UserCategoryProjection.user_id == uid, UserCategoryProjection.sync_id == "c1"))
            assert db.scalar(select(UserTagProjection).where(
                UserTagProjection.user_id == uid, UserTagProjection.sync_id == "tg1"))
            assert db.scalar(select(ReadBudgetProjection).where(
                ReadBudgetProjection.ledger_id == lid, ReadBudgetProjection.sync_id == "bu1"))
            assert db.scalar(select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == lid, ReadTxProjection.sync_id == "t1"))
    finally:
        app.dependency_overrides.clear()
