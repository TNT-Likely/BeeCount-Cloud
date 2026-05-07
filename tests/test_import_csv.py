"""账本导入端到端测试 —— upload / preview / execute(SSE) + parser unit。

设计:.docs/web-ledger-import.md §3.9
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadTxProjection
from src.services.import_data import (
    ImportFieldMapping,
    apply_mapping,
    parse_csv_text,
)
from src.services.import_data.cache import clear_all
from src.services.import_data.parsers.beecount import BeeCountParser
from src.services.import_data.parsers.generic import GenericParser


# ──────────────────── infra ────────────────────


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


def _login(client: TestClient, email: str = "imp@test.com") -> str:
    client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": "d-web",
            "client_type": "web",
            "device_name": "pytest",
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
            "device_name": "pytest",
            "platform": "test",
        },
    )
    return r.json()["access_token"]


def _make_ledger(client: TestClient, token: str) -> str:
    r = client.post(
        "/api/v1/write/ledgers",
        json={"ledger_name": "imp", "currency": "CNY"},
        headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
    )
    return r.json()["entity_id"]


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_all()
    yield
    clear_all()


# ──────────────────── unit: parsers ────────────────────


def test_beecount_parser_sniff_and_mapping():
    csv = (
        "Type,Category,Subcategory,Amount,Account,From Account,To Account,Note,Time,Tags,Attachments\n"
        "Expense,餐饮,午餐,35.00,招行,,,星巴克,2024-05-01 12:30:00,商务,\n"
    )
    data = parse_csv_text(raw_text=csv)
    assert data.source_format == "beecount"
    assert data.suggested_mapping.required_complete
    assert data.suggested_mapping.tx_type is not None
    assert data.suggested_mapping.amount is not None


def test_generic_parser_fuzzy_columns():
    csv = "类型,金额,时间,备注\n支出,35.00,2024-05-01 12:30,星巴克\n"
    data = parse_csv_text(raw_text=csv)
    assert data.source_format == "generic"
    assert data.suggested_mapping.tx_type == "类型"
    assert data.suggested_mapping.amount == "金额"
    assert data.suggested_mapping.happened_at == "时间"
    assert data.suggested_mapping.note == "备注"


def test_alipay_columns_via_generic():
    """支付宝特有列名 → generic parser 应该识别(全集 alias)。"""
    csv = (
        "交易时间,商品说明,金额(元),收/支,类别,收/付款方式\n"
        "2024-05-01,星巴克,35,支出,餐饮,招行\n"
    )
    data = parse_csv_text(raw_text=csv)
    assert data.source_format == "generic"
    m = data.suggested_mapping
    assert m.tx_type == "收/支"
    assert m.amount == "金额(元)"
    assert m.happened_at == "交易时间"
    assert m.category_name == "类别"
    assert m.note == "商品说明"


def test_wechat_columns_via_generic():
    csv = (
        "交易时间,交易类型,商品,金额(元),收/支\n"
        "2024-05-01,商品消费,星巴克,35,支出\n"
    )
    data = parse_csv_text(raw_text=csv)
    assert data.source_format == "generic"
    m = data.suggested_mapping
    assert m.tx_type == "收/支"
    assert m.happened_at == "交易时间"
    # 「交易类型」当作 category_name(wechat 这列实际意思就是分类)
    assert m.category_name == "交易类型"


def test_generic_does_not_sniff_branded():
    p = GenericParser()
    assert p.sniff("anything") is False


# ──────────────────── unit: transformer ────────────────────


def test_apply_mapping_happy_path():
    csv = "类型,金额,时间,分类,备注\n支出,35.00,2024-05-01 12:30,餐饮,星巴克\n"
    data = parse_csv_text(raw_text=csv)
    mapping = data.suggested_mapping
    txs, errors, _ = apply_mapping(rows=data.rows, mapping=mapping)
    assert errors == []
    assert len(txs) == 1
    assert txs[0].tx_type == "expense"
    assert txs[0].amount == Decimal("35.00")
    assert txs[0].note == "星巴克"
    assert txs[0].category_name == "餐饮"


def test_apply_mapping_required_field_missing():
    csv = "金额,时间\n35,2024-05-01\n"  # 没 type 列
    data = parse_csv_text(raw_text=csv)
    mapping = data.suggested_mapping
    # generic parser 认不出 tx_type
    assert mapping.tx_type is None
    txs, errors, _ = apply_mapping(rows=data.rows, mapping=mapping)
    # 必填映射不全 → 单条 PARSE_MAPPING_INCOMPLETE
    assert len(errors) == 1
    assert errors[0].code == "PARSE_MAPPING_INCOMPLETE"
    assert txs == []


def test_apply_mapping_user_override():
    """generic parser 没识别 tx_type 时,用户手填映射应该工作。"""
    csv = "状态,金额,时间,分类\n支出,35,2024-05-01,餐饮\n"
    data = parse_csv_text(raw_text=csv)
    # 默认 mapping 推断不出 tx_type
    assert data.suggested_mapping.tx_type is None
    # 用户手填:状态 列 = tx_type
    mapping = ImportFieldMapping(
        tx_type="状态",
        amount="金额",
        happened_at="时间",
        category_name="分类",
    )
    txs, errors, _ = apply_mapping(rows=data.rows, mapping=mapping)
    assert errors == []
    assert len(txs) == 1
    assert txs[0].tx_type == "expense"


def test_datetime_explicit_format():
    """歧义日期 1/2/2024 在 MM/DD vs DD/MM 应该解析出不同月份。"""
    csv = "类型,金额,时间,分类\n支出,35,1/2/2024,餐饮\n"
    data = parse_csv_text(raw_text=csv)
    # MM/DD/YYYY → 1月2日
    m_us = ImportFieldMapping(
        tx_type="类型", amount="金额", happened_at="时间", category_name="分类",
        datetime_format="%m/%d/%Y",
    )
    txs, _, _ = apply_mapping(rows=data.rows, mapping=m_us)
    assert txs[0].happened_at.month == 1
    assert txs[0].happened_at.day == 2
    # DD/MM/YYYY → 2月1日
    m_eu = ImportFieldMapping(
        tx_type="类型", amount="金额", happened_at="时间", category_name="分类",
        datetime_format="%d/%m/%Y",
    )
    txs2, _, _ = apply_mapping(rows=data.rows, mapping=m_eu)
    assert txs2[0].happened_at.month == 2
    assert txs2[0].happened_at.day == 1


def test_expense_is_negative_option():
    """- 35.00 + expense_is_negative=True → tx_type=expense + amount=35。"""
    csv = "类型,金额,时间,分类\n,−35.00,2024-05-01,餐饮\n"  # type 空,靠负数判断
    data = parse_csv_text(raw_text=csv)
    mapping = ImportFieldMapping(
        tx_type="类型", amount="金额", happened_at="时间", category_name="分类",
        expense_is_negative=True,
        strip_currency_symbols=True,
    )
    txs, errors, _ = apply_mapping(rows=data.rows, mapping=mapping)
    assert errors == [], errors
    assert txs[0].tx_type == "expense"
    assert txs[0].amount == Decimal("35.00")


def test_xlsx_parse():
    """openpyxl 解析 .xlsx → 跟 CSV 走同一条路径,headers + rows 等价。"""
    from openpyxl import Workbook
    import io as _io

    wb = Workbook()
    ws = wb.active
    ws.append(["类型", "金额", "时间", "分类", "备注"])
    ws.append(["支出", 35.5, "2024-05-01 12:30", "餐饮", "星巴克"])
    ws.append(["收入", 8000, "2024-05-05", "工资", "5月工资"])
    buf = _io.BytesIO()
    wb.save(buf)

    from src.services.import_data import parse_excel_bytes
    data = parse_excel_bytes(payload=buf.getvalue())
    assert data.source_format == "generic"
    assert len(data.rows) == 2
    txs, errors, _ = apply_mapping(rows=data.rows, mapping=data.suggested_mapping)
    assert errors == [], errors
    assert len(txs) == 2
    assert txs[0].tx_type == "expense"
    assert txs[0].amount == Decimal("35.5")
    assert txs[1].tx_type == "income"
    assert txs[1].amount == Decimal("8000")


def test_currency_symbol_stripped():
    # 千分位 1,234 必须 quote 才能合法 CSV
    csv = '类型,金额,时间,分类\n支出,"¥1,234.50",2024-05-01,餐饮\n'
    data = parse_csv_text(raw_text=csv)
    txs, errors, _ = apply_mapping(rows=data.rows, mapping=data.suggested_mapping)
    assert errors == [], errors
    assert txs[0].amount == Decimal("1234.50")


# ──────────────────── e2e: upload / preview / execute ────────────────────


def _beecount_csv() -> str:
    return (
        "Type,Category,Subcategory,Amount,Account,From Account,To Account,Note,Time,Tags,Attachments\n"
        "Expense,Food,Lunch,35.00,招行,,,星巴克,2024-05-01 12:30:00,,\n"
        "Expense,Food,Coffee,28.00,微信,,,瑞幸,2024-05-02 09:15:00,,\n"
        "Income,Salary,,8000.00,招行,,,5月工资,2024-05-05 18:00:00,,\n"
    )


def test_upload_and_preview_happy_path():
    client = _make_client()
    try:
        token = _login(client, "imp1@test.com")
        ledger_id = _make_ledger(client, token)

        files = {"file": ("test.csv", _beecount_csv().encode("utf-8"), "text/csv")}
        r = client.post(
            "/api/v1/import/upload",
            files=files,
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source_format"] == "beecount"
        assert body["stats"]["total_rows"] == 3
        assert body["stats"]["by_type"]["expense_count"] == 2
        assert body["stats"]["by_type"]["income_count"] == 1
        assert body["import_token"]
        assert len(body["sample_rows"]) == 3
    finally:
        app.dependency_overrides.clear()


def test_preview_recompute_when_target_ledger_changes():
    client = _make_client()
    try:
        token = _login(client, "imp2@test.com")
        ledger_a = _make_ledger(client, token)

        files = {"file": ("test.csv", _beecount_csv().encode("utf-8"), "text/csv")}
        r = client.post(
            "/api/v1/import/upload",
            files=files,
            data={"target_ledger_id": ledger_a},
            headers={"Authorization": f"Bearer {token}"},
        )
        token_id = r.json()["import_token"]
        # 默认 target → ledger_a,无现有数据,所有 accounts 都是新建
        assert len(r.json()["stats"]["accounts"]["new_names"]) >= 1

        # 在 ledger_a 上手动建一个 account "招行" → 再 preview,应该统计为 matched
        client.post(
            f"/api/v1/write/ledgers/{ledger_a}/accounts",
            json={"name": "招行", "currency": "CNY", "base_change_id": 0},
            headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
        )
        r2 = client.post(
            f"/api/v1/import/{token_id}/preview",
            json={"target_ledger_id": ledger_a},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 200, r2.text
        accounts = r2.json()["stats"]["accounts"]
        assert "招行" in accounts["matched_names"]
        assert "招行" not in accounts["new_names"]
    finally:
        app.dependency_overrides.clear()


def test_upload_too_large_returns_413():
    client = _make_client()
    try:
        token = _login(client, "imp3@test.com")
        big = b"x" * (11 * 1024 * 1024)
        files = {"file": ("big.csv", big, "text/csv")}
        r = client.post(
            "/api/v1/import/upload",
            files=files,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 413, r.text
    finally:
        app.dependency_overrides.clear()


def test_upload_no_rows_returns_400():
    client = _make_client()
    try:
        token = _login(client, "imp4@test.com")
        files = {"file": ("empty.csv", b"header_only\n", "text/csv")}
        r = client.post(
            "/api/v1/import/upload",
            files=files,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_token_expired_returns_410():
    client = _make_client()
    try:
        token = _login(client, "imp5@test.com")
        r = client.post(
            "/api/v1/import/imp_does_not_exist/preview",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 410, r.text
    finally:
        app.dependency_overrides.clear()


def test_second_upload_evicts_first():
    """同 user 二次上传 — 第一个 token 应失效。"""
    client = _make_client()
    try:
        token = _login(client, "imp6@test.com")
        ledger_id = _make_ledger(client, token)

        # 第一次
        r = client.post(
            "/api/v1/import/upload",
            files={"file": ("a.csv", _beecount_csv().encode("utf-8"), "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        first_token = r.json()["import_token"]

        # 第二次
        r2 = client.post(
            "/api/v1/import/upload",
            files={"file": ("b.csv", _beecount_csv().encode("utf-8"), "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        second_token = r2.json()["import_token"]
        assert first_token != second_token

        # 第一个应该 expired
        r3 = client.post(
            f"/api/v1/import/{first_token}/preview",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r3.status_code == 410
    finally:
        app.dependency_overrides.clear()


def test_execute_creates_transactions_and_atomic_rollback_on_error(monkeypatch):
    """integration: execute 走通 + mock 中间抛错验证整体回滚。"""
    client = _make_client()
    try:
        token = _login(client, "imp7@test.com")
        ledger_id = _make_ledger(client, token)

        # upload
        r = client.post(
            "/api/v1/import/upload",
            files={"file": ("ok.csv", _beecount_csv().encode("utf-8"), "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        import_token = r.json()["import_token"]

        # execute happy path (SSE)
        with client.stream(
            "POST",
            f"/api/v1/import/{import_token}/execute",
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            assert resp.status_code == 200, resp.read()
            events = list(_iter_sse(resp))

        complete = next((e for e in events if e["event"] == "complete"), None)
        assert complete is not None, events
        assert complete["data"]["created_tx_count"] == 3
        assert complete["data"]["skipped_count"] == 0

        # 验证写入
        db = next(app.dependency_overrides[get_db]())
        try:
            txs = db.scalars(select(ReadTxProjection)).all()
            assert len(txs) == 3, [t.note for t in txs]
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_execute_rollback_when_mutator_raises(monkeypatch):
    """mock create_transaction 在第 2 笔抛错 → 整批 rollback,ledger 仍空。"""
    from src.routers.import_data import endpoints as ep

    call_count = {"n": 0}
    real_create_transaction = ep.create_transaction

    def boom(snapshot, payload):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise ValueError("boom on 2nd row")
        return real_create_transaction(snapshot, payload)

    monkeypatch.setattr(ep, "create_transaction", boom)

    client = _make_client()
    try:
        token = _login(client, "imp8@test.com")
        ledger_id = _make_ledger(client, token)

        r = client.post(
            "/api/v1/import/upload",
            files={"file": ("ok.csv", _beecount_csv().encode("utf-8"), "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        import_token = r.json()["import_token"]

        with client.stream(
            "POST",
            f"/api/v1/import/{import_token}/execute",
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            assert resp.status_code == 200, resp.read()
            events = list(_iter_sse(resp))

        err = next((e for e in events if e["event"] == "error"), None)
        assert err is not None, events
        assert err["data"]["code"] == "WRITE_TX_FAILED"

        # Ledger 应该完全没数据(整体回滚)
        db = next(app.dependency_overrides[get_db]())
        try:
            txs = db.scalars(select(ReadTxProjection)).all()
            assert txs == [], [t.note for t in txs]
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_cancel_token():
    client = _make_client()
    try:
        token = _login(client, "imp9@test.com")
        ledger_id = _make_ledger(client, token)
        r = client.post(
            "/api/v1/import/upload",
            files={"file": ("a.csv", _beecount_csv().encode("utf-8"), "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        import_token = r.json()["import_token"]

        rd = client.delete(
            f"/api/v1/import/{import_token}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert rd.status_code == 200
        assert rd.json()["cancelled"] is True

        # token 已不存在
        r2 = client.post(
            f"/api/v1/import/{import_token}/preview",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 410
    finally:
        app.dependency_overrides.clear()


# ──────────────────── helper: SSE parser ────────────────────


def _iter_sse(resp):
    """简单解析 text/event-stream chunks → [{event, data}, ...]"""
    buf = ""
    for chunk in resp.iter_text():
        buf += chunk
    for block in buf.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = ""
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data += line[len("data:"):].strip()
        if not event:
            continue
        try:
            parsed = json.loads(data) if data else {}
        except json.JSONDecodeError:
            parsed = {"raw": data}
        yield {"event": event, "data": parsed}
