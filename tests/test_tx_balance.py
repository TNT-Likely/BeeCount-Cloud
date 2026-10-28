"""资产流水余额:with_balance 计算每笔交易完成后主账户余额。

- 核心逻辑单测:`_compute_balance_after_map` / `_tx_effect_on_account`。
- 端到端冒烟:建账本 + 建账户(initial_balance) + 批量建交易,打
  `?with_balance=true` 读交易,断言 balance_after 返回且数值正确;并验证
  不带 with_balance 时该字段为 null。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadTxProjection, UserAccountProjection
from src.routers.read.workspace import (
    _compute_balance_after_map,
    _expense_leg_deltas,
    _transfer_account_deltas,
    _transfer_fee_only_deltas,
    _tx_effect_on_account,
)

_TZ = timezone.utc


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=_TZ)


# ---------------------------------------------------------------------------
# 单测:纯函数逻辑(不依赖网络/auth)
# ---------------------------------------------------------------------------
def _make_session() -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed(session, ledger_id: int = 1, user_id: str = "u1") -> list[ReadTxProjection]:
    acc = UserAccountProjection(
        sync_id="acc1", user_id=user_id, name="现金", initial_balance=100.0
    )
    session.add(acc)
    rows = [
        ReadTxProjection(
            ledger_id=ledger_id, sync_id="t1", user_id=user_id, tx_type="expense",
            amount=30.0, happened_at=_dt(2026, 1, 1), tx_index=1,
            account_sync_id="acc1", account_name="现金", source_change_id=0,
        ),
        ReadTxProjection(
            ledger_id=ledger_id, sync_id="t2", user_id=user_id, tx_type="income",
            amount=50.0, happened_at=_dt(2026, 2, 1), tx_index=2,
            account_sync_id="acc1", account_name="现金", source_change_id=0,
        ),
        ReadTxProjection(
            ledger_id=ledger_id, sync_id="t3", user_id=user_id, tx_type="transfer",
            amount=20.0, happened_at=_dt(2026, 3, 1), tx_index=3,
            from_account_sync_id="acc1", from_account_name="现金",
            to_account_sync_id="acc2", to_account_name="储蓄", source_change_id=0,
        ),
        ReadTxProjection(
            ledger_id=ledger_id, sync_id="t4", user_id=user_id, tx_type="expense",
            amount=10.0, happened_at=_dt(2026, 4, 1), tx_index=4,
            account_sync_id="accX", account_name="其它", source_change_id=0,
        ),
    ]
    for r in rows:
        session.add(r)
    session.commit()
    return rows


def test_tx_effect_on_account():
    tx = ReadTxProjection(
        tx_type="expense", amount=30.0, account_sync_id="a", from_account_sync_id="f",
        to_account_sync_id="t",
    )
    assert _tx_effect_on_account(tx, "a") == -30.0
    assert _tx_effect_on_account(tx, "other") == 0.0
    inc = ReadTxProjection(tx_type="income", amount=50.0, account_sync_id="a")
    assert _tx_effect_on_account(inc, "a") == 50.0
    tr = ReadTxProjection(tx_type="transfer", amount=20.0, from_account_sync_id="f", to_account_sync_id="t")
    assert _tx_effect_on_account(tr, "f") == -20.0
    assert _tx_effect_on_account(tr, "t") == 20.0


def test_transfer_account_deltas_fee_modes():
    """转账手续费三种模式对账户的完整余额增量。"""
    # deduct_from_source: 转出方额外扣 fee(到账=amount)
    d = _transfer_account_deltas(
        100.0, "f", "t", '{"amount":5.0,"mode":"deduct_from_source"}'
    )
    assert d == {"f": -105.0, "t": 100.0}
    # deduct_from_target: 到账方少到 fee(到账=amount-fee)
    d = _transfer_account_deltas(
        100.0, "f", "t", '{"amount":5.0,"mode":"deduct_from_target"}'
    )
    assert d == {"f": -100.0, "t": 95.0}
    # separate_account: 手续费账户另扣
    d = _transfer_account_deltas(
        100.0, "f", "t",
        '{"amount":5.0,"mode":"separate_account","feeAccountId":"x"}',
    )
    assert d == {"f": -100.0, "t": 100.0, "x": -5.0}
    # discount(0026 优惠): 转出方少扣 fee(实扣 amount-fee),到账方全收
    d = _transfer_account_deltas(
        50.0, "f", "t", '{"amount":20.0,"mode":"discount"}'
    )
    assert d == {"f": -30.0, "t": 50.0}
    # 无 fee / 缺失 → 退化为普通转账
    assert _transfer_account_deltas(100.0, "f", "t", None) == {"f": -100.0, "t": 100.0}
    assert _transfer_account_deltas(100.0, "f", "t", "") == {"f": -100.0, "t": 100.0}


def test_transfer_fee_only_deltas():
    """聚合余额在 SQL 基础额之上叠加的纯 fee 增量。"""
    assert _transfer_fee_only_deltas(
        '{"amount":5.0,"mode":"deduct_from_source"}', "f", "t"
    ) == {"f": -5.0}
    assert _transfer_fee_only_deltas(
        '{"amount":5.0,"mode":"deduct_from_target"}', "f", "t"
    ) == {"t": -5.0}
    assert _transfer_fee_only_deltas(
        '{"amount":5.0,"mode":"separate_account","feeAccountId":"x"}', "f", "t"
    ) == {"x": -5.0}
    # discount(0026): 被"少扣"方是转出方,增量是 +fee(抵消 SQL 基础 -amount)
    assert _transfer_fee_only_deltas(
        '{"amount":20.0,"mode":"discount"}', "f", "t"
    ) == {"f": 20.0}
    assert _transfer_fee_only_deltas(None, "f", "t") == {}
    # 被扣方缺失 → 不产生增量(不污染他人余额)
    assert _transfer_fee_only_deltas(
        '{"amount":5.0,"mode":"deduct_from_source"}', None, "t"
    ) == {}


def test_expense_leg_deltas_account_id():
    """合并付款 legs 按腿账户分摊扣款(accountId 口径)。"""
    json_legs = (
        '[{"accountId":"accA","accountName":"支付宝","amount":60.0},'
        '{"accountId":"accB","accountName":"微信","amount":40.0}]'
    )
    d = _expense_leg_deltas(json_legs, None)
    assert d == {"accA": -60.0, "accB": -40.0}
    # 无 legs / 非法 → 空
    assert _expense_leg_deltas(None, None) == {}
    assert _expense_leg_deltas("not-json", None) == {}
    assert _expense_leg_deltas("[]", None) == {}


def test_expense_leg_deltas_account_name():
    """导入 legs 仅带 accountName,靠 name_to_id 反查 syncId。"""
    json_legs = '[{"accountName":"支付宝","amount":60.0},{"accountName":"微信","amount":40.0}]'
    name_to_id = {"支付宝": "accA", "微信": "accB"}
    d = _expense_leg_deltas(json_legs, name_to_id)
    assert d == {"accA": -60.0, "accB": -40.0}
    # 名字反查不到 → 该腿被跳过,不污染余额
    d2 = _expense_leg_deltas(json_legs, {"支付宝": "accA"})
    assert d2 == {"accA": -60.0}


def test_tx_effect_on_account_merged_payment():
    """支出合并付款:各腿账户按其份额扣,主账户若非腿则不重复扣全额。"""
    legs = (
        '[{"accountId":"accA","accountName":"A","amount":60.0},'
        '{"accountId":"accB","accountName":"B","amount":40.0}]'
    )
    tx = ReadTxProjection(
        tx_type="expense", amount=100.0, account_sync_id="main", payment_legs_json=legs
    )
    assert _tx_effect_on_account(tx, "accA") == -60.0
    assert _tx_effect_on_account(tx, "accB") == -40.0
    assert _tx_effect_on_account(tx, "main") == 0.0  # 主账户非腿,不重复扣


def test_compute_balance_after_merged_payment_statement():
    """合并付款:腿账户的「对账单」running balance 应计入其份额。"""
    Session = _make_session()
    db = Session()
    try:
        for a in (
            UserAccountProjection(sync_id="main", user_id="u1", name="主账户", initial_balance=0.0),
            UserAccountProjection(sync_id="accA", user_id="u1", name="A", initial_balance=0.0),
            UserAccountProjection(sync_id="accB", user_id="u1", name="B", initial_balance=0.0),
        ):
            db.add(a)
        legs = (
            '[{"accountId":"accA","accountName":"A","amount":60.0},'
            '{"accountId":"accB","accountName":"B","amount":40.0}]'
        )
        rows = [
            ReadTxProjection(
                ledger_id=1, sync_id="t1", user_id="u1", tx_type="expense",
                amount=100.0, happened_at=_dt(2026, 3, 1), tx_index=1,
                account_sync_id="main", account_name="主账户",
                payment_legs_json=legs, source_change_id=0,
            ),
        ]
        for r in rows:
            db.add(r)
        db.commit()
        name_to_id = {"主账户": "main", "A": "accA", "B": "accB"}
        # 腿账户 A:对账单应看到 -60(结构 {account: {tx: balance}})
        rA = _compute_balance_after_map(db, [1], rows, "u1", "accA", name_to_id)
        assert rA["accA"]["t1"] == -60.0
        # 腿账户 B:-40
        rB = _compute_balance_after_map(db, [1], rows, "u1", "accB", name_to_id)
        assert rB["accB"]["t1"] == -40.0
        # 主账户(非腿):不重复扣,余额 0
        rM = _compute_balance_after_map(db, [1], rows, "u1", "main", name_to_id)
        assert rM["main"]["t1"] == 0.0
    finally:
        db.close()


def test_compute_balance_after_per_row_primary():
    """逐行主账户模式:支出/收入=账户;转账=转出账户。"""
    Session = _make_session()
    db = Session()
    try:
        rows = _seed(db)
        result = _compute_balance_after_map(db, [1], rows, "u1", None)
        # acc1: 100 -30(expense) = 70; +50(income) = 120; -20(transfer 转出) = 100
        # (结构 {account: {tx: balance}})
        assert result["acc1"]["t1"] == 70.0
        assert result["acc1"]["t2"] == 120.0
        assert result["acc1"]["t3"] == 100.0
        # accX 无 projection → 视为初始 0 → -10
        assert result["accX"]["t4"] == -10.0
    finally:
        db.close()


def test_compute_balance_after_statement_mode():
    """账户筛选模式:所有行追踪同一账户,转账双向计入。"""
    Session = _make_session()
    db = Session()
    try:
        rows = _seed(db)
        # 追踪 acc1:与逐行结果一致(结构 {account: {tx: balance}})
        r1 = _compute_balance_after_map(db, [1], rows, "u1", "acc1")
        assert r1["acc1"]["t1"] == 70.0
        assert r1["acc1"]["t2"] == 120.0
        assert r1["acc1"]["t3"] == 100.0
        # 追踪 acc2(转账收款方):仅 t3 入账 +20,初始 0 → 20
        r2 = _compute_balance_after_map(db, [1], rows, "u1", "acc2")
        assert r2["acc2"]["t3"] == 20.0
    finally:
        db.close()


def test_compute_balance_after_max_targets_cap():
    """无筛选且分散账户过多时放弃计算(返回空 dict)。"""
    Session = _make_session()
    db = Session()
    try:
        rows = _seed(db)
        # 4 个分散目标(acc1/acc2/accX + 隐含),cap=1 → 空
        result = _compute_balance_after_map(db, [1], rows, "u1", None, max_targets=1)
        assert result == {}
    finally:
        db.close()


def test_compute_balance_after_separate_fee_account():
    """separate_account 模式:被扣手续费账户(第三账户)的对账单也应计入 -fee。

    之前 SQL 仅按 account/from/to 过滤,漏掉 feeAccountId==target 的转账,
    导致该第三账户的 running balance 看不到手续费扣减;此处锁定补丁行为。
    """
    Session = _make_session()
    db = Session()
    try:
        accs = [
            UserAccountProjection(sync_id="acc1", user_id="u1", name="转出", initial_balance=0.0),
            UserAccountProjection(sync_id="acc2", user_id="u1", name="转入", initial_balance=0.0),
            UserAccountProjection(sync_id="acc3", user_id="u1", name="手续费账户", initial_balance=0.0),
        ]
        for a in accs:
            db.add(a)
        rows = [
            ReadTxProjection(
                ledger_id=1, sync_id="t1", user_id="u1", tx_type="transfer",
                amount=100.0, happened_at=_dt(2026, 3, 1), tx_index=1,
                from_account_sync_id="acc1", from_account_name="转出",
                to_account_sync_id="acc2", to_account_name="转入",
                transfer_fee_json='{"amount":5.0,"mode":"separate_account","feeAccountId":"acc3"}',
                source_change_id=0,
            ),
        ]
        for r in rows:
            db.add(r)
        db.commit()

        r1 = _compute_balance_after_map(db, [1], rows, "u1", "acc1")
        assert r1["acc1"]["t1"] == -100.0
        r2 = _compute_balance_after_map(db, [1], rows, "u1", "acc2")
        assert r2["acc2"]["t1"] == 100.0
        # 第三账户:手续费 -5 应体现在对账单 running balance
        r3 = _compute_balance_after_map(db, [1], rows, "u1", "acc3")
        assert r3["acc3"]["t1"] == -5.0
    finally:
        db.close()


def _make_client() -> TestClient:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
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


def _register_web(client: TestClient, email: str) -> str:
    client.post(
        "/api/v1/auth/register",
        json={
            "email": email, "password": "Pa$$word1!",
            "device_id": "d-web", "client_type": "web",
            "device_name": "pytest", "platform": "test",
        },
    )
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email, "password": "Pa$$word1!",
            "device_id": "d-web", "client_type": "web",
            "device_name": "pytest", "platform": "test",
        },
    )
    return r.json()["access_token"]


def test_workspace_transactions_with_balance():
    client = _make_client()
    try:
        token = _register_web(client, "bal@test.com")
        # 建账本
        r = client.post(
            "/api/v1/write/ledgers",
            json={"ledger_name": "default", "currency": "CNY"},
            headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
        )
        assert r.status_code == 200, r.text
        ledger_id = r.json()["entity_id"]
        # 建账户,initial_balance = 100
        r = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/accounts",
            json={"base_change_id": 0, "name": "现金", "account_type": "cash", "initial_balance": 100.0},
            headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
        )
        assert r.status_code == 200, r.text
        # 批量建两笔支出(均记到"现金"),金额 30 / 50
        r = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/batch",
            json={
                "base_change_id": 0,
                "transactions": [
                    {"tx_type": "expense", "amount": 30.0, "happened_at": "2026-05-01T00:00:00Z",
                     "account_name": "现金", "note": "a"},
                    {"tx_type": "expense", "amount": 50.0, "happened_at": "2026-06-01T00:00:00Z",
                     "account_name": "现金", "note": "b"},
                ],
                "auto_ai_tag": False, "locale": "zh",
            },
            headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
        )
        assert r.status_code == 200, r.text

        # 不带 with_balance → balance_after 为 null
        r = client.get(
            f"/api/v1/read/workspace/transactions?ledger_id={ledger_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert len(items) == 2
        assert all(it["balance_after"] is None for it in items)

        # 带 with_balance=true → 余额非空且数值正确(100-30=70, 70-50=20)
        r = client.get(
            f"/api/v1/read/workspace/transactions?ledger_id={ledger_id}&with_balance=true",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        balances = sorted(it["balance_after"] for it in items)
        assert balances == [20.0, 70.0]
    finally:
        app.dependency_overrides.clear()
