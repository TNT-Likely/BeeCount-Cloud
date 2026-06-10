"""#41: 交易投影 account_sync_id 按账户名补全 — 端到端测试。

覆盖三条写入路径:
  - mobile /sync/push → sync_applier → projection.upsert_tx
  - web POST /write/ledgers/{id}/transactions → _commit_create_tx_fast → projection.upsert_tx
  - alembic 迁移 BACKFILL_STATEMENTS 语义

以及两条契约边界:
  - 同名两账户 → 保持 NULL(宁缺勿错)
  - partial update 不带 account 字段 → 已有 account_sync_id 不被清掉
"""
from __future__ import annotations

import sqlalchemy as sa
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Ledger, ReadTxProjection, UserAccountProjection


# --------------------------------------------------------------------------- #
# Test helpers                                                                 #
# --------------------------------------------------------------------------- #

def _make_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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


def _iso(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat()


def _register(client: TestClient, email: str, client_type: str = "app") -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": client_type,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _seed_ledger(client: TestClient, token: str, device_id: str, ledger_id: str) -> None:
    now = _iso()
    content = (
        f'{{"ledgerName":"{ledger_id}","currency":"CNY","count":0,'
        '"items":[],"accounts":[],"categories":[],"tags":[]}'
    )
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": "ledger_snapshot",
                    "entity_sync_id": ledger_id,
                    "action": "upsert",
                    "payload": {"content": content},
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _push(client: TestClient, token: str, device_id: str, changes: list) -> dict:
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={"device_id": device_id, "changes": changes},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _login_web(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _get_ledger_internal_id(TS, external_id: str) -> str:
    with TS() as db:
        return db.scalar(select(Ledger.id).where(Ledger.external_id == external_id))


def _get_ledger_user_id(TS, external_id: str) -> str:
    with TS() as db:
        return db.scalar(select(Ledger.user_id).where(Ledger.external_id == external_id))


# --------------------------------------------------------------------------- #
# Test 1: mobile push — 只带 accountName 不带 accountId → 按名补全             #
# --------------------------------------------------------------------------- #

def test_mobile_push_tx_name_only_resolves_account() -> None:
    """mobile push 先建 account entity(带 syncId),再 push 只带 accountName 的
    expense tx → 投影行 account_sync_id 应被补全为 acc1。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "fallback_m1@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "FB_LG1")

        # Step 1: push account entity
        _push(client, token, device, [
            {
                "ledger_id": "FB_LG1",
                "entity_type": "account",
                "entity_sync_id": "acc1",
                "action": "upsert",
                "updated_at": _iso(),
                "payload": {"syncId": "acc1", "name": "微信余额", "type": "cash", "currency": "CNY"},
            }
        ])

        # Step 2: push tx with accountName only (no accountId)
        _push(client, token, device, [
            {
                "ledger_id": "FB_LG1",
                "entity_type": "transaction",
                "entity_sync_id": "tx_name_only",
                "action": "upsert",
                "updated_at": _iso(),
                "payload": {
                    "syncId": "tx_name_only",
                    "type": "expense",
                    "amount": 30.0,
                    "happenedAt": _iso(),
                    "accountName": "微信余额",
                    # intentionally no accountId
                },
            }
        ])

        lid = _get_ledger_internal_id(TS, "FB_LG1")
        with TS() as db:
            tx = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == lid,
                    ReadTxProjection.sync_id == "tx_name_only",
                )
            )
            assert tx is not None, "tx projection row missing"
            assert tx.account_name == "微信余额"
            assert tx.account_sync_id == "acc1", (
                f"account_sync_id should be 'acc1' via name fallback, got {tx.account_sync_id!r}"
            )
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Test 2: web write — 只带 account_name 不带 account_id → 按名补全             #
# --------------------------------------------------------------------------- #

def test_web_write_tx_name_only_resolves_account() -> None:
    """web POST /write/ledgers/{id}/transactions 只传 account_name → 投影补全
    account_sync_id。覆盖 _commit_create_tx_fast → projection.upsert_tx 路径。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "fallback_w1@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "FB_WLG1")

        # 先建 account entity via push
        _push(client, token, device, [
            {
                "ledger_id": "FB_WLG1",
                "entity_type": "account",
                "entity_sync_id": "wacc1",
                "action": "upsert",
                "updated_at": _iso(),
                "payload": {"syncId": "wacc1", "name": "支付宝", "type": "cash", "currency": "CNY"},
            }
        ])

        # 取 base_change_id
        web_auth = _login_web(client, "fallback_w1@example.com")
        web_token = web_auth["access_token"]
        web_hdr = {"Authorization": f"Bearer {web_token}", "X-Device-ID": "pytest-web"}

        meta_res = client.get(
            "/api/v1/read/ledgers/FB_WLG1",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert meta_res.status_code == 200, meta_res.text
        base = int(meta_res.json()["source_change_id"])

        # web 只传 account_name
        create_res = client.post(
            "/api/v1/write/ledgers/FB_WLG1/transactions",
            headers=web_hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 99.0,
                "happened_at": _iso(),
                "account_name": "支付宝",
                # no account_id
            },
        )
        assert create_res.status_code == 200, create_res.text
        tx_id = create_res.json()["entity_id"]

        lid = _get_ledger_internal_id(TS, "FB_WLG1")
        with TS() as db:
            tx = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == lid,
                    ReadTxProjection.sync_id == tx_id,
                )
            )
            assert tx is not None, "tx projection row missing"
            assert tx.account_name == "支付宝"
            assert tx.account_sync_id == "wacc1", (
                f"account_sync_id should be 'wacc1' via name fallback, got {tx.account_sync_id!r}"
            )
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Test 3: 同名两账户 → 保持 NULL                                                #
# --------------------------------------------------------------------------- #

def test_name_only_ambiguous_stays_null() -> None:
    """同名两个账户存在时,按名补全不确定 → account_sync_id 保持 NULL。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "fallback_ambig@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "FB_AMBIG")

        # 推两个同名账户
        _push(client, token, device, [
            {
                "ledger_id": "FB_AMBIG",
                "entity_type": "account",
                "entity_sync_id": "dup1",
                "action": "upsert",
                "updated_at": _iso(),
                "payload": {"syncId": "dup1", "name": "现金", "type": "cash", "currency": "CNY"},
            },
            {
                "ledger_id": "FB_AMBIG",
                "entity_type": "account",
                "entity_sync_id": "dup2",
                "action": "upsert",
                "updated_at": _iso(),
                "payload": {"syncId": "dup2", "name": "现金", "type": "cash", "currency": "CNY"},
            },
        ])

        # 推只带同名的 tx
        _push(client, token, device, [
            {
                "ledger_id": "FB_AMBIG",
                "entity_type": "transaction",
                "entity_sync_id": "tx_ambig",
                "action": "upsert",
                "updated_at": _iso(),
                "payload": {
                    "syncId": "tx_ambig",
                    "type": "expense",
                    "amount": 10.0,
                    "happenedAt": _iso(),
                    "accountName": "现金",
                },
            }
        ])

        lid = _get_ledger_internal_id(TS, "FB_AMBIG")
        with TS() as db:
            tx = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == lid,
                    ReadTxProjection.sync_id == "tx_ambig",
                )
            )
            assert tx is not None
            assert tx.account_name == "现金"
            assert tx.account_sync_id is None, (
                f"ambiguous name should keep NULL, got {tx.account_sync_id!r}"
            )
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Test 4: partial update 只改 amount → 已有 account_sync_id 不被清除           #
# --------------------------------------------------------------------------- #

def test_partial_update_keeps_account_sync_id() -> None:
    """先正常 push tx(带 accountId),再 push 同 syncId 只改 amount(不带任何
    account 字段)→ account_sync_id 保持原值。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "fallback_keep@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "FB_KEEP")

        # 正常首次写入(带 id)
        _push(client, token, device, [
            {
                "ledger_id": "FB_KEEP",
                "entity_type": "account",
                "entity_sync_id": "kacc1",
                "action": "upsert",
                "updated_at": _iso(),
                "payload": {"syncId": "kacc1", "name": "银行卡", "type": "bank", "currency": "CNY"},
            },
            {
                "ledger_id": "FB_KEEP",
                "entity_type": "transaction",
                "entity_sync_id": "tx_keep",
                "action": "upsert",
                "updated_at": _iso(),
                "payload": {
                    "syncId": "tx_keep",
                    "type": "expense",
                    "amount": 50.0,
                    "happenedAt": _iso(),
                    "accountId": "kacc1",
                    "accountName": "银行卡",
                },
            },
        ])

        lid = _get_ledger_internal_id(TS, "FB_KEEP")
        with TS() as db:
            tx = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == lid,
                    ReadTxProjection.sync_id == "tx_keep",
                )
            )
            assert tx is not None and tx.account_sync_id == "kacc1"

        # partial update:只改 amount,不带任何 account 字段
        _push(client, token, device, [
            {
                "ledger_id": "FB_KEEP",
                "entity_type": "transaction",
                "entity_sync_id": "tx_keep",
                "action": "upsert",
                "updated_at": _iso(),
                "payload": {
                    "syncId": "tx_keep",
                    "type": "expense",
                    "amount": 99.0,
                    "happenedAt": _iso(),
                    # no accountId, no accountName
                },
            }
        ])

        with TS() as db:
            tx = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == lid,
                    ReadTxProjection.sync_id == "tx_keep",
                )
            )
            assert tx is not None
            assert tx.amount == 99.0, f"amount not updated: {tx.amount}"
            assert tx.account_sync_id == "kacc1", (
                f"partial update must not clear account_sync_id, got {tx.account_sync_id!r}"
            )
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Test 5: transfer — from/to name 各自补全                                      #
# --------------------------------------------------------------------------- #

def test_transfer_from_to_resolved() -> None:
    """transfer tx 只带 from/to 名(无 id)→ from_account_sync_id / to_account_sync_id
    各自按名补全。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "fallback_xfer@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "FB_XFER")

        _push(client, token, device, [
            {
                "ledger_id": "FB_XFER",
                "entity_type": "account",
                "entity_sync_id": "src_acc",
                "action": "upsert",
                "updated_at": _iso(),
                "payload": {"syncId": "src_acc", "name": "现金账户", "type": "cash", "currency": "CNY"},
            },
            {
                "ledger_id": "FB_XFER",
                "entity_type": "account",
                "entity_sync_id": "dst_acc",
                "action": "upsert",
                "updated_at": _iso(),
                "payload": {"syncId": "dst_acc", "name": "储蓄账户", "type": "bank", "currency": "CNY"},
            },
        ])

        # transfer tx 只带名
        _push(client, token, device, [
            {
                "ledger_id": "FB_XFER",
                "entity_type": "transaction",
                "entity_sync_id": "tx_xfer",
                "action": "upsert",
                "updated_at": _iso(),
                "payload": {
                    "syncId": "tx_xfer",
                    "type": "transfer",
                    "amount": 200.0,
                    "happenedAt": _iso(),
                    "fromAccountName": "现金账户",
                    "toAccountName": "储蓄账户",
                    # no fromAccountId / toAccountId
                },
            }
        ])

        lid = _get_ledger_internal_id(TS, "FB_XFER")
        with TS() as db:
            tx = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == lid,
                    ReadTxProjection.sync_id == "tx_xfer",
                )
            )
            assert tx is not None
            assert tx.from_account_sync_id == "src_acc", (
                f"from_account_sync_id should be 'src_acc', got {tx.from_account_sync_id!r}"
            )
            assert tx.to_account_sync_id == "dst_acc", (
                f"to_account_sync_id should be 'dst_acc', got {tx.to_account_sync_id!r}"
            )
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Test 6: alembic 迁移 BACKFILL_STATEMENTS 语义                                #
# --------------------------------------------------------------------------- #

def test_backfill_statements_semantics() -> None:
    """直接验证 BACKFILL_STATEMENTS 的 SQL 语义:
      - NULL id + 唯一名  → 补全
      - NULL id + 同名双账户 → 保持 NULL
      - 已有 id           → 不改写
    """
    import importlib.util
    from pathlib import Path

    migration_path = (
        Path(__file__).parent.parent
        / "alembic" / "versions" / "0015_backfill_tx_account_sync_id.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0015", migration_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    BACKFILL_STATEMENTS = mod.BACKFILL_STATEMENTS

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        # 准备账户数据(source_change_id NOT NULL → 给个 0)
        conn.execute(
            sa.text(
                "INSERT INTO user_account_projection "
                "(user_id, sync_id, name, source_change_id) VALUES "
                "(:u, :s, :n, 0)"
            ),
            [
                {"u": "user1", "s": "acc_unique", "n": "唯一账户"},
                {"u": "user1", "s": "dup_a",      "n": "重名账户"},
                {"u": "user1", "s": "dup_b",      "n": "重名账户"},
            ],
        )

        # 准备 tx 数据(source_change_id NOT NULL → 给个 0):
        # row1: account_sync_id NULL, name=唯一 → 应补全
        # row2: account_sync_id NULL, name=重名 → 保持 NULL
        # row3: account_sync_id 已有值 → 不改写
        conn.execute(
            sa.text(
                "INSERT INTO read_tx_projection "
                "(ledger_id, sync_id, user_id, tx_type, amount, tx_index, happened_at, "
                " account_sync_id, account_name, source_change_id) VALUES "
                "(:lid, :sid, :uid, 'expense', 1.0, 0, '2026-01-01', :aid, :an, 0)"
            ),
            [
                {"lid": "lg1", "sid": "tx1", "uid": "user1", "aid": None,          "an": "唯一账户"},
                {"lid": "lg1", "sid": "tx2", "uid": "user1", "aid": None,          "an": "重名账户"},
                {"lid": "lg1", "sid": "tx3", "uid": "user1", "aid": "existing_id", "an": "唯一账户"},
            ],
        )

        # 执行第一条 backfill (account_sync_id / account_name)
        stmt = BACKFILL_STATEMENTS[0]
        conn.execute(sa.text(stmt))

        rows = conn.execute(
            sa.text(
                "SELECT sync_id, account_sync_id FROM read_tx_projection "
                "WHERE ledger_id='lg1' ORDER BY sync_id"
            )
        ).fetchall()
        by_sid = {r[0]: r[1] for r in rows}

        assert by_sid["tx1"] == "acc_unique", (
            f"tx1: unique name should be resolved, got {by_sid['tx1']!r}"
        )
        assert by_sid["tx2"] is None, (
            f"tx2: ambiguous name should stay NULL, got {by_sid['tx2']!r}"
        )
        assert by_sid["tx3"] == "existing_id", (
            f"tx3: existing id should not be overwritten, got {by_sid['tx3']!r}"
        )
