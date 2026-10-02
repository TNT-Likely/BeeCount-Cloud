"""信用卡紅利回饋自動入帳(§2.9.5.4 MOZE_FEATURE_GAP_SD.md)。跟
`tests/test_credit_card_autopay.py` 同一个模式:直接调用
`services.card_reward_payout.materialize_due_card_reward_payouts(db)`,不经过
HTTP background loop,断言生成的交易 + `CardRewardPayout` 去重台帳 +
notifications 表内容。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import CardRewardPayout, Notification, ReadTxProjection, User
from src.services import card_reward_payout, credit_card


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


def _login(client, email, *, device_id="d1", client_type="app"):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email, "password": "Pa$$word1!", "device_id": device_id,
            "client_type": client_type, "device_name": "pytest", "platform": "test",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d1"):
    body = {
        "ledger_id": ledger_id, "entity_type": entity_type, "entity_sync_id": sync_id,
        "action": "upsert", "updated_at": _iso(), "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": device_id, "changes": [body]})
    assert r.status_code == 200, r.text


def _income_tx_to(TS, account_id):
    with TS() as db:
        rows = db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.tx_type == "income",
                ReadTxProjection.account_sync_id == account_id,
            ).order_by(ReadTxProjection.happened_at.asc())
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _all_tx_in(TS, account_id):
    with TS() as db:
        rows = db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.account_sync_id == account_id,
            ).order_by(ReadTxProjection.happened_at.asc())
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _payout_rows(TS, email, rule_id):
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        rows = db.scalars(
            select(CardRewardPayout).where(
                CardRewardPayout.user_id == user_id, CardRewardPayout.rule_sync_id == rule_id,
            )
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _notifications_for(TS, email):
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        rows = db.scalars(
            select(Notification).where(
                Notification.user_id == user_id, Notification.category == "card_reward",
            )
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _login_and_seed(client, ledger_id, email, *, billing_day=None, payment_due_day=None):
    app_tok = _login(client, email, device_id="d-app", client_type="app")
    web_tok = _login(client, email, device_id="d-web", client_type="web")
    hdr_app = {"Authorization": f"Bearer {app_tok}"}
    hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
    _push(client, hdr_app, ledger_id, "ledger", ledger_id,
          {"syncId": ledger_id, "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
    card_payload = {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY"}
    if billing_day is not None:
        card_payload["billingDay"] = billing_day
    if payment_due_day is not None:
        card_payload["paymentDueDay"] = payment_due_day
    _push(client, hdr_app, ledger_id, "account", "acc-card", card_payload, device_id="d-app")
    _push(client, hdr_app, ledger_id, "account", "acc-wallet",
          {"syncId": "acc-wallet", "name": "點數錢包", "type": "cash", "currency": "CNY"}, device_id="d-app")
    return hdr_app, hdr_web


def _latest_change_id(client, token_hdr, ledger_id):
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}", headers=token_hdr)
    assert r.status_code == 200, r.text
    return int(r.json()["source_change_id"])


def _create_rule(client, hdr_web, ledger_id, *, base=0, **kwargs):
    payload = {
        "base_change_id": base, "label": "測試規則", "rate_type": "percentage", "rate_value": 10.0,
    }
    payload.update(kwargs)
    r = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/accounts/acc-card/card-reward-rules",
        headers=hdr_web, json=payload,
    )
    assert r.status_code == 200, r.text
    return r.json()["entity_id"]


# --------------------------------------------------------------------------- #
# immediate_after_tx / after_posting_date(逐筆結算)                          #
# --------------------------------------------------------------------------- #


def test_immediate_after_tx_pays_only_after_settlement_days_and_dedups():
    client, TS = _make_client()
    try:
        email = "crp1@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp1", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp1",
            settlement_type="immediate_after_tx", settlement_days=3, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgp1", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        now = tx_day + timedelta(days=2)  # 還沒到 settlement_days=3
        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 0

        now2 = tx_day + timedelta(days=3)  # 到期
        with TS() as db:
            result2 = card_reward_payout.materialize_due_card_reward_payouts(db, now=now2)
            db.commit()
        assert result2 == {"tx_payouts": 1, "period_payouts": 0}
        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        assert incomes[0].amount == 10.0  # 100 * 10%

        payouts = _payout_rows(TS, email, rule_id)
        assert len(payouts) == 1
        assert payouts[0].dedup_key == "tx-1"

        # 重跑不重複入帳。
        with TS() as db:
            result3 = card_reward_payout.materialize_due_card_reward_payouts(db, now=now2)
            db.commit()
        assert result3 == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 1
    finally:
        app.dependency_overrides.clear()


def test_immediate_after_tx_applies_total_rounding_to_final_payout():
    """2026-08 使用者反饋 bug #1:選了「總額四捨五入」,逐筆結算的實際入帳
    金額依舊帶小數(例如 13.95 而非 14)——`_materialize_per_tx` 原本完全
    沒有套用 `rule.total_rounding`,只有預覽畫面
    (`card_rewards.compute_account_card_rewards`)有套用,造成預覽跟實際
    入帳金額對不上,使用者以為「四捨五入」設定沒生效。"""
    client, TS = _make_client()
    try:
        email = "crp-round@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp-round", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp-round",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
            rate_value=5.0, total_rounding="round",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgp-round", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 279.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        now = tx_day + timedelta(days=1)
        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 1, "period_payouts": 0}
        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        # 279 * 5% = 13.95,total_rounding="round" 應該取整到整數 14,不是 13.95。
        assert incomes[0].amount == 14.0
    finally:
        app.dependency_overrides.clear()


def test_immediate_after_tx_reward_tx_gets_category_and_source_link():
    """§2.9.5.4 補強(2026-08-04 使用者反饋):①回饋 income 交易自動帶固定
    的「回饋金」分類(不然空白分類很奇怪);②反查 `reward_source_tx_id`
    指向原始消費(取代舊版把 tx sync_id 原樣寫進備註文字裡的做法),web 端
    能靠它渲染可點擊的「關聯消費」連結。"""
    client, TS = _make_client()
    try:
        email = "crp-src@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp-src", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp-src",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgp-src", "transaction", "tx-src-1",
              {"syncId": "tx-src-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result == {"tx_payouts": 1, "period_payouts": 0}

        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        reward_tx = incomes[0]
        assert reward_tx.category_name == "回饋金"
        assert reward_tx.category_kind == "income"
        assert reward_tx.category_sync_id is not None
        assert reward_tx.reward_source_tx_sync_id == "tx-src-1"
        # 舊版備註直接嵌入 tx sync_id,使用者反饋不友善;改版後不再出現在
        # 備註文字裡(改走上面的結構化反查欄位)。
        assert "tx-src-1" not in (reward_tx.note or "")
    finally:
        app.dependency_overrides.clear()


def test_reward_category_reused_not_duplicated_across_payouts():
    """同一個使用者名下,「回饋金」分類只會建一次,之後每次入帳都重用同一個
    sync_id(不管是逐筆結算多次觸發,還是不同規則各自入帳)。"""
    client, TS = _make_client()
    try:
        email = "crp-cat@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp-cat", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp-cat",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        for i in range(2):
            tx_day = datetime.now(timezone.utc) - timedelta(days=1)
            _push(client, hdr_app, "lgp-cat", "transaction", f"tx-cat-{i}",
                  {"syncId": f"tx-cat-{i}", "type": "expense", "amount": 50.0, "happenedAt": _iso(tx_day),
                   "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
                  device_id="d-app")
        with TS() as db:
            card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()

        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 2
        category_ids = {tx.category_sync_id for tx in incomes}
        assert len(category_ids) == 1
        assert None not in category_ids

        from src.models import UserCategoryProjection

        with TS() as db:
            user_id = db.scalar(select(User.id).where(User.email == email))
            rows = db.scalars(
                select(UserCategoryProjection).where(
                    UserCategoryProjection.user_id == user_id,
                    UserCategoryProjection.name == "回饋金",
                )
            ).all()
        assert len(rows) == 1
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_reward_source_tx_id_partial_update_keeps_existing_value():
    """跟 rewardRuleIds/debtId 同款契约:partial update 只带其它字段时,
    `rewardSourceTxId` 缺键要保留既有值,不能被静默清空(既有 bug 类型,見
    `write/_shared.py::_projection_row_to_tx_dict` 跟
    `snapshot_builder.py` 的漏 SELECT 注释)。"""
    client, TS = _make_client()
    try:
        email = "crp-merge@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp-merge", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp-merge",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgp-merge", "transaction", "tx-merge-1",
              {"syncId": "tx-merge-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        with TS() as db:
            card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        reward_tx_id = incomes[0].sync_id

        # partial update:只带 note,不带 rewardSourceTxId。
        _push(client, hdr_app, "lgp-merge", "transaction", reward_tx_id,
              {"syncId": reward_tx_id, "note": "改個備注"}, device_id="d-app")

        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(ReadTxProjection.sync_id == reward_tx_id)
            )
            assert row is not None
            assert row.note == "改個備注"
            assert row.reward_source_tx_sync_id == "tx-merge-1"
    finally:
        app.dependency_overrides.clear()


def test_web_patch_other_field_keeps_reward_source_tx_id():
    """跟上面 mobile push 那條契約測試對應的 web fast path 版本
    (`_commit_write_fast_tx` → `_projection_row_to_tx_dict`)——PATCH 回饋
    入帳交易的備註時,不帶 `rewardSourceTxId` 不能把它靜默清空(既有 bug
    類型,同 §2.9.5.4 之前踩過的 `settlement_type` 漏 SELECT)。"""
    client, TS = _make_client()
    try:
        email = "crp-fast@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp-fast", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp-fast",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgp-fast", "transaction", "tx-fast-1",
              {"syncId": "tx-fast-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        with TS() as db:
            card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        reward_tx_id = incomes[0].sync_id

        base = _latest_change_id(client, hdr_web, "lgp-fast")
        res = client.patch(
            f"/api/v1/write/ledgers/lgp-fast/transactions/{reward_tx_id}",
            headers=hdr_web,
            json={"base_change_id": base, "note": "web 端改備注"},
        )
        assert res.status_code == 200, res.text

        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(ReadTxProjection.sync_id == reward_tx_id)
            )
            assert row is not None
            assert row.note == "web 端改備注"
            assert row.reward_source_tx_sync_id == "tx-fast-1"
            assert row.category_name == "回饋金"
    finally:
        app.dependency_overrides.clear()


def test_after_posting_date_currently_behaves_same_as_immediate_after_tx():
    """§2.10 延後入帳(deferred_posting_at)還沒實作,after_posting_date 目前
    行為等同 immediate_after_tx,見 card_rewards.compute_settlement_date
    docstring。這條測試鎖住現況,§2.10 落地後要重跑。"""
    client, TS = _make_client()
    try:
        email = "crp2@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp2", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp2",
            settlement_type="after_posting_date", settlement_days=2, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=2)
        _push(client, hdr_app, "lgp2", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 50.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result == {"tx_payouts": 1, "period_payouts": 0}
        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        assert incomes[0].amount == 5.0
    finally:
        app.dependency_overrides.clear()


def test_immediate_after_tx_cap_amount_clamps_and_dedups_zero_amount():
    client, TS = _make_client()
    try:
        email = "crp3@t.com"
        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        hdr_app, hdr_web = _login_and_seed(client, "lgp3", email, billing_day=billing_day, payment_due_day=10)
        rule_id = _create_rule(
            client, hdr_web, "lgp3",
            settlement_type="immediate_after_tx", settlement_days=0,
            reward_account_id="acc-wallet", cap_amount=15.0,
        )
        # 兩筆時間錯開,確保 _qualifying_transactions 依 happened_at 排序後
        # 順序穩定(不依賴同一時間戳下 SQLite 的 tie-break 行為)。
        _push(client, hdr_app, "lgp3", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        _push(client, hdr_app, "lgp3", "transaction", "tx-2",
              {"syncId": "tx-2", "type": "expense", "amount": 100.0,
               "happenedAt": _iso(now + timedelta(minutes=1)),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 2, "period_payouts": 0}

        incomes = _income_tx_to(TS, "acc-wallet")
        # 第一筆 10(未超上限),第二筆被夾到剩餘 5(15-10),第二筆入帳金額 5。
        assert sorted(t.amount for t in incomes) == [5.0, 10.0]

        payouts = {p.dedup_key: p.amount for p in _payout_rows(TS, email, rule_id)}
        assert payouts == {"tx-1": 10.0, "tx-2": 5.0}

        # 額度用完,第三筆完全不產生入帳交易,但仍記去重(金額 0)。
        _push(client, hdr_app, "lgp3", "transaction", "tx-3",
              {"syncId": "tx-3", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        with TS() as db:
            result2 = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result2 == {"tx_payouts": 1, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 2  # 第三筆沒有新增 income 交易
        payouts2 = {p.dedup_key: p.amount for p in _payout_rows(TS, email, rule_id)}
        assert payouts2["tx-3"] == 0.0

        # 再重跑一次,tx-3 已經記過去重,不會被重複評估。
        with TS() as db:
            result3 = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result3 == {"tx_payouts": 0, "period_payouts": 0}
    finally:
        app.dependency_overrides.clear()


def test_immediate_after_tx_self_credit_offsets_billing():
    """reward_account_id 指回卡片自己(最常見用例——直接折抵當期帳單)。

    2026-08-04 使用者反饋修復:payout 引擎生成的 income 交易日期必須是
    「規則算出的入帳日」(`tx_happened_at + settlement_days`),不能是
    「排程實際執行的時間點」(`now`)——不然使用者事後補記一筆很久以前的
    消費(比如今天補記上個月的帳),回饋交易會被錯誤地記在「今天」,而不是
    使用者設定的入帳時機該落在的那一天。這裡 `settlement_days=0`,原始消費
    (`tx-1`)刻意放在已結束的週期內,所以回饋應該落在**同一個**已結束週期
    裡,反映在 `remaining_due`(終身跑動餘額)/`statement_amount`(該週期
    窗口)雙雙減少 20,而不是被計入還在累積中的 `open_cycle_spend`(修復前
    的錯誤行為——回饋交易被錯誤地蓋上 `now` 的時間戳,才會落到當下這個還在
    累積的週期)。"""
    client, TS = _make_client()
    try:
        email = "crp4@t.com"
        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=10)).day
        hdr_app, hdr_web = _login_and_seed(client, "lgp4", email, billing_day=billing_day, payment_due_day=25)
        rule_id = _create_rule(
            client, hdr_web, "lgp4",
            settlement_type="immediate_after_tx", settlement_days=0,
            reward_account_id="acc-card",
        )
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        spend_at = datetime.combine(cycle_start, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        _push(client, hdr_app, "lgp4", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 200.0, "happenedAt": _iso(spend_at),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        billing_before = client.get(
            "/api/v1/read/ledgers/lgp4/accounts/acc-card/billing-summary", headers=hdr_web,
        ).json()
        assert billing_before["open_cycle_spend"] == 0.0
        assert billing_before["remaining_due"] == 200.0
        assert billing_before["statement_amount"] == 200.0

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 1, "period_payouts": 0}

        billing_after = client.get(
            "/api/v1/read/ledgers/lgp4/accounts/acc-card/billing-summary", headers=hdr_web,
        ).json()
        # 20 元回饋(200*10%)日期落在原始消費所屬的那個已結束週期(settlement_
        # days=0),所以計入 remaining_due/statement_amount,不是 open_cycle_
        # spend(那個窗口完全沒有任何交易落在裡面,理當維持 0)。
        assert billing_after["remaining_due"] == 180.0
        assert billing_after["statement_amount"] == 180.0
        assert billing_after["open_cycle_spend"] == 0.0

        # 回饋交易自己的 happened_at 應該等於 tx-1 的消費日期(settlement_
        # days=0),不是 payout 引擎實際跑的時間點 `now`。
        with TS() as db:
            reward_tx = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == db.scalar(
                        select(ReadTxProjection.ledger_id).where(ReadTxProjection.sync_id == "tx-1")
                    ),
                    ReadTxProjection.tx_type == "income",
                )
            )
            assert reward_tx is not None
            happened = reward_tx.happened_at
            if happened.tzinfo is None:
                happened = happened.replace(tzinfo=timezone.utc)
            assert happened.date() == spend_at.date()
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# period_end(區間結束後整批結算)                                             #
# --------------------------------------------------------------------------- #


def test_period_end_pays_once_after_cycle_closes_and_dedups():
    client, TS = _make_client()
    try:
        email = "crp5@t.com"
        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=10)).day
        hdr_app, hdr_web = _login_and_seed(client, "lgp5", email, billing_day=billing_day, payment_due_day=25)
        rule_id = _create_rule(
            client, hdr_web, "lgp5",
            settlement_type="period_end", reward_account_id="acc-wallet",
        )
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        spend_at = datetime.combine(cycle_start, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        _push(client, hdr_app, "lgp5", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 300.0, "happenedAt": _iso(spend_at),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 1}
        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        assert incomes[0].amount == 30.0  # 300 * 10%

        notifs = _notifications_for(TS, email)
        assert len(notifs) == 1
        assert notifs[0].payload_json["ruleId"] == rule_id

        # 重跑不重複入帳、不重複通知。
        with TS() as db:
            result2 = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result2 == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 1
        assert len(_notifications_for(TS, email)) == 1
    finally:
        app.dependency_overrides.clear()


def test_period_end_tops_up_when_reward_rule_bound_after_settlement():
    """整期已結算後才替消費補綁回饋規則:補發差額(不重複發已入帳的部分),
    再重跑不會再補。"""
    client, TS = _make_client()
    try:
        email = "crp5b@t.com"
        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=10)).day
        hdr_app, hdr_web = _login_and_seed(client, "lgp5b", email, billing_day=billing_day, payment_due_day=25)
        rule_id = _create_rule(
            client, hdr_web, "lgp5b",
            settlement_type="period_end", reward_account_id="acc-wallet",
        )
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        spend_at = datetime.combine(cycle_start, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        _push(client, hdr_app, "lgp5b", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 300.0, "happenedAt": _iso(spend_at),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        # 第二筆當時忘了選回饋規則。
        _push(client, hdr_app, "lgp5b", "transaction", "tx-2",
              {"syncId": "tx-2", "type": "expense", "amount": 200.0, "happenedAt": _iso(spend_at),
               "accountId": "acc-card", "accountName": "信用卡"},
              device_id="d-app")
        with TS() as db:
            card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert [t.amount for t in _income_tx_to(TS, "acc-wallet")] == [30.0]

        # 事後補綁:重跑要補發差額 20(200 * 10%),且只補一次。
        _push(client, hdr_app, "lgp5b", "transaction", "tx-2",
              {"syncId": "tx-2", "type": "expense", "amount": 200.0, "happenedAt": _iso(spend_at),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 1}
        assert sorted(t.amount for t in _income_tx_to(TS, "acc-wallet")) == [20.0, 30.0]

        with TS() as db:
            result2 = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result2 == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 2
    finally:
        app.dependency_overrides.clear()


def test_period_end_skips_when_no_billing_schedule():
    client, TS = _make_client()
    try:
        email = "crp6@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp6", email)  # 没设 billing_day
        _create_rule(
            client, hdr_web, "lgp6",
            settlement_type="period_end", reward_account_id="acc-wallet",
        )
        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 0
    finally:
        app.dependency_overrides.clear()


def test_period_end_delayed_settlement_offset_waits_and_backfills_older_period():
    """2026-08-12 使用者反饋:規則設定「次二月28日」入帳(`settlement_month_
    offset=2`, `settlement_day_of_month=28`),結果 7 月的消費在週期一結束
    (7/31)就直接入帳了,完全沒等到 9/28。根因是 `_materialize_period_end`
    只看 `period_offset=-1`(剛結束的那一期)且忽略 `compute_settlement_date`
    ——這裡鎖住修正後的行為:①還沒到規則設定的入帳日之前,即使那期已經
    結束也不能入帳;②真正到了入帳日時,`period_offset=-1` 早就滑到更新的
    週期,必須往回找到原本那期才能補上,入帳交易的 `happened_at` 要是規則
    算出來的入帳日(2026-03-28),不是週期結束日(2026-01-31)。"""
    client, TS = _make_client()
    try:
        email = "crp-delay@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp-delay", email)  # calendar_month 不需要 billing_day
        rule_id = _create_rule(
            client, hdr_web, "lgp-delay",
            interval="calendar_month", settlement_type="period_end",
            settlement_month_offset=2, settlement_day_of_month=28,
            reward_account_id="acc-wallet",
        )
        spend_at = datetime(2026, 1, 15, tzinfo=timezone.utc)
        _push(client, hdr_app, "lgp-delay", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 300.0, "happenedAt": _iso(spend_at),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        # 週期(1月)已經結束,但入帳日(3/28)還沒到——不該入帳。
        too_early = datetime(2026, 2, 10, tzinfo=timezone.utc)
        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=too_early)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 0

        # 到了入帳日(3/28)——這時候 period_offset=-1 相對 3 月已經是 2 月,
        # 必須往回多看才找得到 1 月那期。
        due_now = datetime(2026, 3, 28, tzinfo=timezone.utc)
        with TS() as db:
            result2 = card_reward_payout.materialize_due_card_reward_payouts(db, now=due_now)
            db.commit()
        assert result2 == {"tx_payouts": 0, "period_payouts": 1}
        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        assert incomes[0].amount == 30.0  # 300 * 10%
        assert incomes[0].happened_at.date() == date(2026, 3, 28)

        # 重跑不重複入帳。
        with TS() as db:
            result3 = card_reward_payout.materialize_due_card_reward_payouts(db, now=due_now)
            db.commit()
        assert result3 == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 1
    finally:
        app.dependency_overrides.clear()


def test_period_end_cross_card_cap_shared_key_pools_rules():
    client, TS = _make_client()
    try:
        email = "crp7@t.com"
        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=10)).day
        hdr_app, hdr_web = _login_and_seed(client, "lgp7", email, billing_day=billing_day, payment_due_day=25)
        _push(client, hdr_app, "lgp7", "account", "acc-card2",
              {"syncId": "acc-card2", "name": "副卡", "type": "credit_card", "currency": "CNY",
               "billingDay": billing_day, "paymentDueDay": 25}, device_id="d-app")

        r1 = client.post(
            "/api/v1/write/ledgers/lgp7/accounts/acc-card/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "主卡", "rate_type": "percentage", "rate_value": 10.0,
                "cap_amount": 25.0, "cap_shared_key": "grp",
                "settlement_type": "period_end", "reward_account_id": "acc-wallet",
            },
        )
        assert r1.status_code == 200, r1.text
        rule1_id = r1.json()["entity_id"]
        r2 = client.post(
            "/api/v1/write/ledgers/lgp7/accounts/acc-card2/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": r1.json()["new_change_id"], "label": "副卡",
                "rate_type": "percentage", "rate_value": 10.0,
                "cap_amount": 25.0, "cap_shared_key": "grp",
                "settlement_type": "period_end", "reward_account_id": "acc-wallet",
            },
        )
        assert r2.status_code == 200, r2.text
        rule2_id = r2.json()["entity_id"]

        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        spend_at = datetime.combine(cycle_start, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        _push(client, hdr_app, "lgp7", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 200.0, "happenedAt": _iso(spend_at),
               "accountId": "acc-card", "accountName": "主卡", "rewardRuleIds": [rule1_id]},
              device_id="d-app")
        _push(client, hdr_app, "lgp7", "transaction", "tx-2",
              {"syncId": "tx-2", "type": "expense", "amount": 200.0, "happenedAt": _iso(spend_at),
               "accountId": "acc-card2", "accountName": "副卡", "rewardRuleIds": [rule2_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 2}

        incomes = _income_tx_to(TS, "acc-wallet")
        # raw 各 20,合计 40 > 共享上限 25,各半分攤 20/40*25=12.5。
        assert sorted(t.amount for t in incomes) == [12.5, 12.5]
    finally:
        app.dependency_overrides.clear()


def test_period_end_calendar_month_settles_both_months_when_billing_cycle_spans_two():
    """Phase 22(2026-08 使用者反饋):`calendar_month` 規則的帳單週期橫跨兩
    個自然月時(帳單日 11 號,`billing_cycle_containing` 算出 8/12~9/11 這種
    視窗),`_materialize_period_end` 現在一個 `period_offset` 會對應到這條
    規則的 1~2 個 period 結果(見 `card_rewards._resolve_periods`),兩個月
    要各自入帳一次,不能只挑第一個(`next(...)`)漏掉另一個月。"""
    client, TS = _make_client()
    try:
        email = "crp-ph22a@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp-ph22a", email, billing_day=11, payment_due_day=25)
        rule_id = _create_rule(
            client, hdr_web, "lgp-ph22a",
            interval="calendar_month", settlement_type="period_end", reward_account_id="acc-wallet",
        )
        _push(client, hdr_app, "lgp-ph22a", "transaction", "tx-aug",
              {"syncId": "tx-aug", "type": "expense", "amount": 300.0,
               "happenedAt": "2026-08-20T12:00:00+00:00",
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        _push(client, hdr_app, "lgp-ph22a", "transaction", "tx-sep",
              {"syncId": "tx-sep", "type": "expense", "amount": 100.0,
               "happenedAt": "2026-09-02T12:00:00+00:00",
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        # period_offset=-1(相對 10/5)是帳單週期 8/11~9/11,拆成 8 月+9 月
        # 兩個完整自然月,兩者的入帳日(8/31、9/30)都已經過了。
        now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 1}

        incomes = _income_tx_to(TS, "acc-wallet")
        assert sorted(t.amount for t in incomes) == [10.0, 30.0]  # 100*10%、300*10%

        payouts = _payout_rows(TS, email, rule_id)
        assert sorted(p.dedup_key for p in payouts) == ["2026-08-31", "2026-09-30"]

        # 重跑不重複入帳(兩個月各自都已經記過去重)。
        with TS() as db:
            result2 = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result2 == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 2
    finally:
        app.dependency_overrides.clear()


def test_period_end_lookback_does_not_double_pay_month_seen_in_two_overlapping_cycles():
    """Phase 22 迴歸測試:`calendar_month` 規則 + `settlement_month_offset`
    造成的多期回看(`lookback > 1`)時,相鄰的帳單週期視窗可能重疊到同一個
    自然月(例如 offset=-1 對應 9~10 月、offset=-2 對應 8~9 月,9 月同時出現
    在兩次迭代裡)。修好前:`already_paid` 只在迴圈開始前查一次,同一輪掃描
    內第二次遇到 9 月會被誤判成「還沒付」而重複入帳;修好後:入帳當下立刻
    更新記憶體裡的 `already_paid` 集合,第二次遇到同一個月會被正確擋下。"""
    client, TS = _make_client()
    try:
        email = "crp-ph22b@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp-ph22b", email, billing_day=11, payment_due_day=25)
        # settlement_month_offset=1(不設 settlement_day_of_month,入帳時機
        # 仍是「期末當天」,只是把 lookback 拉到 2,讓同一輪掃描會檢查
        # offset=-1、-2 兩個帳單週期視窗)。
        rule_id = _create_rule(
            client, hdr_web, "lgp-ph22b",
            interval="calendar_month", settlement_type="period_end", reward_account_id="acc-wallet",
            settlement_month_offset=1,
        )
        _push(client, hdr_app, "lgp-ph22b", "transaction", "tx-aug",
              {"syncId": "tx-aug", "type": "expense", "amount": 100.0,
               "happenedAt": "2026-08-20T12:00:00+00:00",
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        _push(client, hdr_app, "lgp-ph22b", "transaction", "tx-sep",
              {"syncId": "tx-sep", "type": "expense", "amount": 100.0,
               "happenedAt": "2026-09-15T12:00:00+00:00",
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        _push(client, hdr_app, "lgp-ph22b", "transaction", "tx-oct",
              {"syncId": "tx-oct", "type": "expense", "amount": 100.0,
               "happenedAt": "2026-10-20T12:00:00+00:00",
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        # now=2026-11-05:offset=-1 是帳單週期 9/11~10/11(拆成 9月+10月),
        # offset=-2 是 8/11~9/11(拆成 8月+9月)——9 月同時出現在兩次迭代。
        now = datetime(2026, 11, 5, tzinfo=timezone.utc)
        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 1}

        incomes = _income_tx_to(TS, "acc-wallet")
        # 修好前:9 月會被入帳兩次(4 筆,10*2 重複),總額多算 10。
        assert sorted(t.amount for t in incomes) == [10.0, 10.0, 10.0]

        payouts = _payout_rows(TS, email, rule_id)
        assert sorted(p.dedup_key for p in payouts) == ["2026-08-31", "2026-09-30", "2026-10-31"]
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# manual / enabled / expired 邊界                                            #
# --------------------------------------------------------------------------- #


def test_manual_settlement_type_never_pays_out():
    client, TS = _make_client()
    try:
        email = "crp8@t.com"
        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        hdr_app, hdr_web = _login_and_seed(client, "lgp8", email, billing_day=billing_day, payment_due_day=10)
        rule_id = _create_rule(client, hdr_web, "lgp8")  # 預設 settlement_type="manual"
        _push(client, hdr_app, "lgp8", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 0
        assert len(_income_tx_to(TS, "acc-card")) == 0
    finally:
        app.dependency_overrides.clear()


def test_disabled_rule_never_pays_out():
    client, TS = _make_client()
    try:
        email = "crp9@t.com"
        now = datetime.now(timezone.utc)
        hdr_app, hdr_web = _login_and_seed(client, "lgp9", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp9",
            settlement_type="immediate_after_tx", settlement_days=0,
            reward_account_id="acc-wallet", enabled=False,
        )
        _push(client, hdr_app, "lgp9", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 0
    finally:
        app.dependency_overrides.clear()


def test_expired_rule_still_pays_for_transaction_made_while_active():
    """入帳看交易當下是否符合資格,不因規則事後過期(ends_at 已過)被追回。"""
    client, TS = _make_client()
    try:
        email = "crp10@t.com"
        now = datetime.now(timezone.utc)
        hdr_app, hdr_web = _login_and_seed(client, "lgp10", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp10",
            settlement_type="immediate_after_tx", settlement_days=0,
            reward_account_id="acc-wallet",
            ends_at=_iso(now - timedelta(days=1)),  # 已過期
        )
        tx_at = now - timedelta(days=5)  # 過期前發生的交易
        _push(client, hdr_app, "lgp10", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_at),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 1, "period_payouts": 0}
        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        assert incomes[0].amount == 10.0
    finally:
        app.dependency_overrides.clear()


def test_internal_task_endpoint_reports_reward_payout_counts():
    client, TS = _make_client()
    try:
        admin_email = "crp-admin@t.com"
        app_tok = _login(client, admin_email, device_id="d-app", client_type="app")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        now = datetime.now(timezone.utc)
        _push(client, hdr_app, "lgp11", "ledger", "lgp11",
              {"syncId": "lgp11", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgp11", "account", "acc-card",
              {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY"},
              device_id="d-app")
        _push(client, hdr_app, "lgp11", "account", "acc-wallet",
              {"syncId": "acc-wallet", "name": "點數錢包", "type": "cash", "currency": "CNY"},
              device_id="d-app")

        # is_admin 要在登入拿 token 之前就设好(admin 状态烙在 JWT claim 里,
        # 见 test_backup_admin_api.py::_bootstrap_admin 同款先设 flag 再登入)。
        with TS() as db:
            from src.models import User as UserModel
            user = db.scalar(select(UserModel).where(UserModel.email == admin_email))
            user.is_admin = True
            db.commit()
        web_tok = _login(client, admin_email, device_id="d-web", client_type="web")
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        rule_id = _create_rule(
            client, hdr_web, "lgp11",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        _push(client, hdr_app, "lgp11", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        r = client.post("/api/v1/internal/tasks/materialize-recurring", headers=hdr_web)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["card_reward_tx_payouts"] == 1
        assert body["card_reward_period_payouts"] == 0
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 退款(2026-08-04 使用者反饋):退款交易自動歸類「退款」分類 + 已入帳的      #
# 信用卡回饋要跟著沖銷,不管排程是退款前還是退款後跑                          #
# --------------------------------------------------------------------------- #


def test_refund_before_payout_excludes_tx_from_future_settlement():
    """退款發生在排程還沒跑到結算日之前 —— 被退款的消費從此排除在
    `_qualifying_transactions` 之外,排程晚點跑到也不會再把它排入結算,
    不需要任何沖銷(因為根本沒有入帳過)。"""
    client, TS = _make_client()
    try:
        email = "crp-ref1@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgpr1", email)
        rule_id = _create_rule(
            client, hdr_web, "lgpr1",
            settlement_type="immediate_after_tx", settlement_days=3, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgpr1", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        base = _latest_change_id(client, hdr_web, "lgpr1")
        r = client.post(
            "/api/v1/write/ledgers/lgpr1/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base, "tx_type": "income", "amount": 100.0,
                "happened_at": _iso(), "account_id": "acc-card", "refund_of_id": "tx-1",
            },
        )
        assert r.status_code == 200, r.text

        # 排程晚點才跑到(超過 settlement_days),tx-1 已經被退款排除,不入帳。
        now2 = tx_day + timedelta(days=3)
        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now2)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 0
        assert len(_payout_rows(TS, email, rule_id)) == 0
    finally:
        app.dependency_overrides.clear()


def test_refund_after_payout_reverses_reward_and_auto_categorizes():
    """退款發生在排程已經跑過、回饋已經入帳之後 —— 退款當下同一個 DB
    transaction 裡補一筆沖銷交易,把已經拿到的回饋沖銷掉;退款交易本身跟
    沖銷交易都自動歸到自建的「退款」分類。"""
    client, TS = _make_client()
    try:
        email = "crp-ref2@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgpr2", email)
        rule_id = _create_rule(
            client, hdr_web, "lgpr2",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgpr2", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result == {"tx_payouts": 1, "period_payouts": 0}
        assert [t.amount for t in _income_tx_to(TS, "acc-wallet")] == [10.0]

        base = _latest_change_id(client, hdr_web, "lgpr2")
        r = client.post(
            "/api/v1/write/ledgers/lgpr2/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base, "tx_type": "income", "amount": 100.0,
                "happened_at": _iso(), "account_id": "acc-card", "refund_of_id": "tx-1",
            },
        )
        assert r.status_code == 200, r.text
        refund_tx_id = r.json()["entity_id"]

        with TS() as db:
            refund_row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == refund_tx_id))
            assert refund_row is not None
            assert refund_row.category_name == "退款"
            assert refund_row.category_kind == "income"
            assert refund_row.category_sync_id is not None

        # 沖銷交易:expense,金額等於已入帳的回饋金,落在回饋原本入帳的帳戶。
        expenses = [t for t in _all_tx_in(TS, "acc-wallet") if t.tx_type == "expense"]
        assert len(expenses) == 1
        clawback = expenses[0]
        assert clawback.amount == 10.0
        assert clawback.category_name == "退款"
        assert clawback.category_kind == "expense"
        assert clawback.reward_source_tx_sync_id == "tx-1"

        # 沖銷不影響原本的去重記錄(那筆 payout 歷史還在,不是被撤銷/刪除)。
        payouts = _payout_rows(TS, email, rule_id)
        assert len(payouts) == 1

        # 重跑排程不會重複沖銷(沖銷只在退款建立當下觸發一次,不是排程行為;
        # tx-1 也已經被 `_qualifying_transactions` 排除,不會被重新評估)。
        with TS() as db:
            result2 = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result2 == {"tx_payouts": 0, "period_payouts": 0}
        assert len([t for t in _all_tx_in(TS, "acc-wallet") if t.tx_type == "expense"]) == 1
    finally:
        app.dependency_overrides.clear()


def test_partial_refund_before_payout_settles_net_amount():
    """部分退款發生在排程還沒跑到結算日之前(2026-09-20 使用者反饋,對齊
    App 端 `_summarizeRulePeriod` 按淨額比例扣減的修法):不再像全額退款
    那樣整筆排除,`_qualifying_transactions` 改用「原始金額 − 已退款金額」
    的淨額算回饋——消費 100、退款 40,淨額 60,10% 規則應該入帳 6.0 而不是
    10.0 或 0。"""
    client, TS = _make_client()
    try:
        email = "crp-pref1@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgppr1", email)
        rule_id = _create_rule(
            client, hdr_web, "lgppr1",
            settlement_type="immediate_after_tx", settlement_days=3, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgppr1", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        base = _latest_change_id(client, hdr_web, "lgppr1")
        r = client.post(
            "/api/v1/write/ledgers/lgppr1/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base, "tx_type": "income", "amount": 40.0,
                "happened_at": _iso(), "account_id": "acc-card", "refund_of_id": "tx-1",
            },
        )
        assert r.status_code == 200, r.text

        now2 = tx_day + timedelta(days=3)
        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now2)
            db.commit()
        assert result == {"tx_payouts": 1, "period_payouts": 0}
        assert [t.amount for t in _income_tx_to(TS, "acc-wallet")] == [6.0]
        payouts = _payout_rows(TS, email, rule_id)
        assert len(payouts) == 1
        assert payouts[0].amount == 6.0
    finally:
        app.dependency_overrides.clear()


def test_partial_refund_after_payout_reverses_only_proportional_reward():
    """部分退款發生在排程已經跑過、回饋已經按退款前的全額入帳之後——沖銷
    金額不是整筆 payout,是「原回饋 − 用淨額重算的回饋」差額:消費 100 已
    入帳 10.0(10%),退款 40 後淨額 60 應得 6.0,所以只沖銷 4.0,使用者仍
    保留 6.0 的回饋淨額。"""
    client, TS = _make_client()
    try:
        email = "crp-pref2@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgppr2", email)
        rule_id = _create_rule(
            client, hdr_web, "lgppr2",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgppr2", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result == {"tx_payouts": 1, "period_payouts": 0}
        assert [t.amount for t in _income_tx_to(TS, "acc-wallet")] == [10.0]

        base = _latest_change_id(client, hdr_web, "lgppr2")
        r = client.post(
            "/api/v1/write/ledgers/lgppr2/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base, "tx_type": "income", "amount": 40.0,
                "happened_at": _iso(), "account_id": "acc-card", "refund_of_id": "tx-1",
            },
        )
        assert r.status_code == 200, r.text

        expenses = [t for t in _all_tx_in(TS, "acc-wallet") if t.tx_type == "expense"]
        assert len(expenses) == 1
        clawback = expenses[0]
        assert clawback.amount == 4.0
        assert clawback.category_name == "退款"
        assert clawback.category_kind == "expense"
        assert clawback.reward_source_tx_sync_id == "tx-1"

        # 原本的去重記錄維持不變(沖銷是另一筆獨立交易,不是改寫歷史 payout)。
        payouts = _payout_rows(TS, email, rule_id)
        assert len(payouts) == 1
        assert payouts[0].amount == 10.0

        # 重跑排程不會再對 tx-1 做任何動作:已經在 `already_paid` 去重集合裡。
        with TS() as db:
            result2 = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result2 == {"tx_payouts": 0, "period_payouts": 0}
        assert len([t for t in _all_tx_in(TS, "acc-wallet") if t.tx_type == "expense"]) == 1
    finally:
        app.dependency_overrides.clear()


def test_partial_refund_with_fixed_amount_rule_keeps_full_reward():
    """`fixed_amount` 規則不隨金額縮放(比照 App 端 `estimateCardRewardForRule`
    對 fixed_amount 的既有語意):部分退款只要淨額仍 > 0,固定回饋維持不變、
    不用比例重算,只有淨額真的歸零(全額退款)才會沖銷掉。"""
    client, TS = _make_client()
    try:
        email = "crp-pref3@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgppr3", email)
        rule_id = _create_rule(
            client, hdr_web, "lgppr3",
            rate_type="fixed_amount", rate_value=15.0,
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgppr3", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result == {"tx_payouts": 1, "period_payouts": 0}
        assert [t.amount for t in _income_tx_to(TS, "acc-wallet")] == [15.0]

        base = _latest_change_id(client, hdr_web, "lgppr3")
        r = client.post(
            "/api/v1/write/ledgers/lgppr3/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base, "tx_type": "income", "amount": 40.0,
                "happened_at": _iso(), "account_id": "acc-card", "refund_of_id": "tx-1",
            },
        )
        assert r.status_code == 200, r.text

        # 淨額 60 > 0,fixed_amount 回饋維持 15 不變,不用沖銷。
        expenses = [t for t in _all_tx_in(TS, "acc-wallet") if t.tx_type == "expense"]
        assert len(expenses) == 0
    finally:
        app.dependency_overrides.clear()


def test_plain_refund_without_reward_rule_gets_refund_category():
    """跟信用卡回饋完全無關的普通退款,也要自動歸到「退款」分類——income/
    expense 各自獨立一份(退款可能是任一方向,見 §2.6 反向类型设计)。"""
    client, TS = _make_client()
    try:
        email = "crp-ref3@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgpr3", email)
        _push(client, hdr_app, "lgpr3", "transaction", "tx-plain-1",
              {"syncId": "tx-plain-1", "type": "expense", "amount": 50.0, "happenedAt": _iso(),
               "accountId": "acc-wallet", "accountName": "點數錢包"},
              device_id="d-app")

        base = _latest_change_id(client, hdr_web, "lgpr3")
        r = client.post(
            "/api/v1/write/ledgers/lgpr3/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base, "tx_type": "income", "amount": 50.0,
                "happened_at": _iso(), "account_id": "acc-wallet", "refund_of_id": "tx-plain-1",
            },
        )
        assert r.status_code == 200, r.text
        refund_tx_id = r.json()["entity_id"]
        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == refund_tx_id))
            assert row.category_name == "退款"
            assert row.category_kind == "income"

        # 反過來:income 交易被退成 expense,分类各自独立一份(不是共用一行)。
        _push(client, hdr_app, "lgpr3", "transaction", "tx-plain-income",
              {"syncId": "tx-plain-income", "type": "income", "amount": 30.0, "happenedAt": _iso(),
               "accountId": "acc-wallet", "accountName": "點數錢包"},
              device_id="d-app")
        base2 = _latest_change_id(client, hdr_web, "lgpr3")
        r2 = client.post(
            "/api/v1/write/ledgers/lgpr3/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base2, "tx_type": "expense", "amount": 30.0,
                "happened_at": _iso(), "account_id": "acc-wallet", "refund_of_id": "tx-plain-income",
            },
        )
        assert r2.status_code == 200, r2.text
        refund_tx_id2 = r2.json()["entity_id"]
        with TS() as db:
            row2 = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == refund_tx_id2))
            assert row2.category_name == "退款"
            assert row2.category_kind == "expense"

            from src.models import UserCategoryProjection

            user_id = db.scalar(select(User.id).where(User.email == email))
            cats = db.scalars(
                select(UserCategoryProjection).where(
                    UserCategoryProjection.user_id == user_id,
                    UserCategoryProjection.name == "退款",
                )
            ).all()
        assert {c.kind for c in cats} == {"income", "expense"}
    finally:
        app.dependency_overrides.clear()


def test_refund_respects_explicit_category_choice():
    """退款表单如果用户自己选了分类,原样尊重,不被自动归类覆盖。"""
    client, TS = _make_client()
    try:
        email = "crp-ref4@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgpr4", email)
        _push(client, hdr_app, "lgpr4", "transaction", "tx-explicit-1",
              {"syncId": "tx-explicit-1", "type": "expense", "amount": 40.0, "happenedAt": _iso(),
               "accountId": "acc-wallet", "accountName": "點數錢包"},
              device_id="d-app")
        _push(client, hdr_app, "lgpr4", "category", "cat-custom",
              {"syncId": "cat-custom", "name": "自訂分類", "kind": "income"}, device_id="d-app")

        base = _latest_change_id(client, hdr_web, "lgpr4")
        r = client.post(
            "/api/v1/write/ledgers/lgpr4/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base, "tx_type": "income", "amount": 40.0,
                "happened_at": _iso(), "account_id": "acc-wallet", "refund_of_id": "tx-explicit-1",
                "category_id": "cat-custom", "category_name": "自訂分類", "category_kind": "income",
            },
        )
        assert r.status_code == 200, r.text
        refund_tx_id = r.json()["entity_id"]
        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == refund_tx_id))
            assert row.category_name == "自訂分類"
    finally:
        app.dependency_overrides.clear()


def test_patch_newly_marking_tx_as_refund_reverses_reward_once():
    """PATCH 把一筆既有交易改成退款(`refund_of_id` 從空變非空)也要觸發
    同一套沖銷 + 自動歸類邏輯;之後再 PATCH 別的欄位(`refund_of_id` 原樣
    帶回)不能重複沖銷。"""
    client, TS = _make_client()
    try:
        email = "crp-ref5@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgpr5", email)
        rule_id = _create_rule(
            client, hdr_web, "lgpr5",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgpr5", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        with TS() as db:
            card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert len(_income_tx_to(TS, "acc-wallet")) == 1

        base = _latest_change_id(client, hdr_web, "lgpr5")
        r = client.post(
            "/api/v1/write/ledgers/lgpr5/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base, "tx_type": "income", "amount": 100.0,
                "happened_at": _iso(), "account_id": "acc-card",
            },
        )
        assert r.status_code == 200, r.text
        tx_id = r.json()["entity_id"]
        base2 = r.json()["new_change_id"]

        r2 = client.patch(
            f"/api/v1/write/ledgers/lgpr5/transactions/{tx_id}",
            headers=hdr_web,
            json={"base_change_id": base2, "refund_of_id": "tx-1"},
        )
        assert r2.status_code == 200, r2.text
        base3 = r2.json()["new_change_id"]

        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == tx_id))
            assert row.category_name == "退款"

        expenses = [t for t in _all_tx_in(TS, "acc-wallet") if t.tx_type == "expense"]
        assert len(expenses) == 1

        # 再 PATCH 一次別的欄位,refund_of_id 原樣帶回,不應該重複沖銷。
        r3 = client.patch(
            f"/api/v1/write/ledgers/lgpr5/transactions/{tx_id}",
            headers=hdr_web,
            json={"base_change_id": base3, "refund_of_id": "tx-1", "note": "改個備注"},
        )
        assert r3.status_code == 200, r3.text
        expenses2 = [t for t in _all_tx_in(TS, "acc-wallet") if t.tx_type == "expense"]
        assert len(expenses2) == 1
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 2026-09-25 退款沖銷口徑:App 同步的退款也沖銷回饋、沖銷交易掛 refundOfId   #
# --------------------------------------------------------------------------- #


def _paid_reward_scenario(client, TS, ledger_id, email):
    hdr_app, hdr_web = _login_and_seed(client, ledger_id, email)
    rule_id = _create_rule(
        client, hdr_web, ledger_id,
        settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
    )
    tx_day = datetime.now(timezone.utc) - timedelta(days=1)
    _push(client, hdr_app, ledger_id, "transaction", "tx-1",
          {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
           "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
          device_id="d-app")
    with TS() as db:
        card_reward_payout.materialize_due_card_reward_payouts(db)
        db.commit()
    payout_tx = _income_tx_to(TS, "acc-wallet")
    assert [t.amount for t in payout_tx] == [10.0]
    return hdr_app, hdr_web, rule_id, payout_tx[0].sync_id


def test_mobile_push_refund_after_payout_reverses_reward_once():
    """App 建的退款走 /sync/push 上來,也要跟 web 建退款一樣沖銷已入帳的回饋金;
    沖銷交易掛 refundOfId 指回回饋入帳交易。重推同一筆退款不重複沖銷。"""
    client, TS = _make_client()
    try:
        email = "crp-push-ref@t.com"
        hdr_app, _hdr_web, _rule, payout_sid = _paid_reward_scenario(client, TS, "lgpush1", email)

        refund = {"syncId": "tx-r", "type": "income", "amount": 100.0, "happenedAt": _iso(),
                  "accountId": "acc-card", "accountName": "信用卡", "refundOfId": "tx-1"}
        r = client.post("/api/v1/sync/push", headers=hdr_app, json={"device_id": "d-app", "changes": [{
            "ledger_id": "lgpush1", "entity_type": "transaction", "entity_sync_id": "tx-r",
            "action": "upsert", "updated_at": _iso(), "payload": refund,
        }]})
        assert r.status_code == 200, r.text
        server_cursor = r.json()["server_cursor"]

        reversals = [t for t in _all_tx_in(TS, "acc-wallet") if t.tx_type == "expense"]
        assert len(reversals) == 1
        assert reversals[0].amount == 10.0
        assert reversals[0].refund_of_sync_id == payout_sid
        assert reversals[0].reward_source_tx_sync_id == "tx-1"
        # 沖銷交易的 change 要在回傳的 cursor 之後,推送端下次 pull 才拉得到。
        assert reversals[0].source_change_id > server_cursor

        # 重推(例如 App 改了備註)不再沖銷一次
        _push(client, hdr_app, "lgpush1", "transaction", "tx-r",
              {**refund, "note": "改備註"}, device_id="d-app")
        assert len([t for t in _all_tx_in(TS, "acc-wallet") if t.tx_type == "expense"]) == 1
    finally:
        app.dependency_overrides.clear()


def test_analytics_nets_refund_and_reward_reversal():
    """消費 100 → 回饋 10 入帳 → 全額退款:統計上支出、收入都歸零(退款扣回
    支出、回饋沖銷扣回收入),不是「收入 110、支出 110」。"""
    client, TS = _make_client()
    try:
        email = "crp-net@t.com"
        hdr_app, hdr_web, _rule, _payout = _paid_reward_scenario(client, TS, "lgnet1", email)
        base = _latest_change_id(client, hdr_web, "lgnet1")
        r = client.post(
            "/api/v1/write/ledgers/lgnet1/transactions", headers=hdr_web,
            json={"base_change_id": base, "tx_type": "income", "amount": 100.0,
                  "happened_at": _iso(), "account_id": "acc-card", "refund_of_id": "tx-1"},
        )
        assert r.status_code == 200, r.text

        res = client.get("/api/v1/read/workspace/analytics", headers=hdr_web,
                         params={"scope": "month", "metric": "expense"})
        assert res.status_code == 200, res.text
        summary = res.json()["summary"]
        assert summary["expense_total"] == 0.0
        assert summary["income_total"] == 0.0
    finally:
        app.dependency_overrides.clear()


def test_backfill_links_legacy_reward_reversal_to_payout():
    """2026-09-25 以前的沖銷交易沒有 refundOfId:啟動補資料要替它補上,並寫
    一條 upsert SyncChange 讓 App 拉到;冪等,第二次不再動。"""
    from src.models import SyncChange

    client, TS = _make_client()
    try:
        email = "crp-backfill@t.com"
        _hdr_app, hdr_web, _rule, payout_sid = _paid_reward_scenario(client, TS, "lgbf1", email)
        base = _latest_change_id(client, hdr_web, "lgbf1")
        r = client.post(
            "/api/v1/write/ledgers/lgbf1/transactions", headers=hdr_web,
            json={"base_change_id": base, "tx_type": "income", "amount": 100.0,
                  "happened_at": _iso(), "account_id": "acc-card", "refund_of_id": "tx-1"},
        )
        assert r.status_code == 200, r.text
        rev = [t for t in _all_tx_in(TS, "acc-wallet") if t.tx_type == "expense"][0]
        with TS() as db:  # 模擬舊資料:沒有 refundOfId
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == rev.sync_id))
            row.refund_of_sync_id = None
            db.commit()

        now = datetime.now(timezone.utc)
        with TS() as db:
            assert card_reward_payout.backfill_reward_reversal_refund_links(db, now=now) == 1
            db.commit()
        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == rev.sync_id))
            assert row.refund_of_sync_id == payout_sid
            assert row.amount == 10.0
            assert row.reward_source_tx_sync_id == "tx-1"
            change = db.scalar(
                select(SyncChange).where(SyncChange.entity_sync_id == rev.sync_id)
                .order_by(SyncChange.change_id.desc()).limit(1)
            )
            assert change.payload_json["refundOfId"] == payout_sid
            assert change.payload_json["amount"] == 10.0
            assert card_reward_payout.backfill_reward_reversal_refund_links(db, now=now) == 0
    finally:
        app.dependency_overrides.clear()
