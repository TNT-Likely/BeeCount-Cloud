"""GET /workspace/transactions.csv 端到端测试。

设计文档:.docs/web-csv-export-design.md

CSV 跟 mobile lib/pages/data/export_page.dart 严格对齐(11 列 + 本地化表头 + Type
列本地化 + parent/sub 拆分 + 单 Time 列)。

覆盖:
1. 表头 + BOM(默认 en,?lang=zh-CN 切中文)
2. Type 列本地化(收入/支出/转账 vs Income/Expense/Transfer)
3. Sub-category 拆列:level=2 → Category 写父类、SubCategory 写自己
4. transfer 行:Account/Category/SubCategory 空,FromAccount/ToAccount 填
5. CSV 转义(逗号 / 双引号)
6. 中文 ledger 名 RFC 5987 文件名 + ASCII fallback
7. 时区折算(Time 列按 tz_offset_minutes)
8. filter 跟列表 endpoint 一致(tx_type)
"""
from __future__ import annotations

from datetime import datetime, timezone

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


def _register_and_token(client: TestClient, email: str, *, device_id: str, client_type: str) -> str:
    client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": device_id,
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": "test",
        },
    )
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": device_id,
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": "test",
        },
    )
    return r.json()["access_token"]


def _iso(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat()


def _push(client, hdr, device_id, changes):
    r = client.post(
        "/api/v1/sync/push",
        headers=hdr,
        json={"device_id": device_id, "changes": changes},
    )
    assert r.status_code == 200, r.text


def _seed_basic(client, hdr, device_id, ledger_id, ledger_name="个人账本"):
    """seed:1 expense(二级分类) + 1 income(一级分类) + 1 transfer。"""
    _push(client, hdr, device_id, [
        # ledger — sync_applier 期望 ledgerName(camelCase)
        {
            "ledger_id": ledger_id, "entity_type": "ledger", "entity_sync_id": ledger_id,
            "action": "upsert", "updated_at": _iso(),
            "payload": {"syncId": ledger_id, "ledgerName": ledger_name, "currency": "CNY"},
        },
        # accounts
        {
            "ledger_id": ledger_id, "entity_type": "account", "entity_sync_id": "acc-credit",
            "action": "upsert", "updated_at": _iso(),
            "payload": {"syncId": "acc-credit", "name": "招商信用卡", "type": "credit_card", "currency": "CNY"},
        },
        {
            "ledger_id": ledger_id, "entity_type": "account", "entity_sync_id": "acc-savings",
            "action": "upsert", "updated_at": _iso(),
            "payload": {"syncId": "acc-savings", "name": "余额宝", "type": "savings", "currency": "CNY"},
        },
        # categories — 父类 餐饮(level=1),子类 午餐(level=2 + parent_name=餐饮)
        {
            "ledger_id": ledger_id, "entity_type": "category", "entity_sync_id": "cat-food",
            "action": "upsert", "updated_at": _iso(),
            "payload": {"syncId": "cat-food", "categoryName": "餐饮", "kind": "expense", "level": 1},
        },
        {
            "ledger_id": ledger_id, "entity_type": "category", "entity_sync_id": "cat-lunch",
            "action": "upsert", "updated_at": _iso(),
            "payload": {
                "syncId": "cat-lunch", "categoryName": "午餐", "kind": "expense",
                "level": 2, "parentName": "餐饮",
            },
        },
        {
            "ledger_id": ledger_id, "entity_type": "category", "entity_sync_id": "cat-salary",
            "action": "upsert", "updated_at": _iso(),
            "payload": {"syncId": "cat-salary", "categoryName": "工资", "kind": "income", "level": 1},
        },
        # expense(走子类 — Category 应是 餐饮,SubCategory 应是 午餐)
        {
            "ledger_id": ledger_id, "entity_type": "transaction", "entity_sync_id": "tx-1",
            "action": "upsert", "updated_at": _iso(),
            "payload": {
                "syncId": "tx-1", "type": "expense", "amount": 35.50,
                "happenedAt": "2026-04-15T06:30:00Z",
                "note": "午餐", "categoryName": "午餐", "categoryId": "cat-lunch",
                "accountName": "招商信用卡", "accountId": "acc-credit",
            },
        },
        # income(一级分类 — Category 应是 工资,SubCategory 应空)
        {
            "ledger_id": ledger_id, "entity_type": "transaction", "entity_sync_id": "tx-2",
            "action": "upsert", "updated_at": _iso(),
            "payload": {
                "syncId": "tx-2", "type": "income", "amount": 5000,
                "happenedAt": "2026-04-15T08:00:00Z",
                "note": "工资", "categoryName": "工资", "categoryId": "cat-salary",
                "accountName": "余额宝", "accountId": "acc-savings",
            },
        },
        # transfer
        {
            "ledger_id": ledger_id, "entity_type": "transaction", "entity_sync_id": "tx-3",
            "action": "upsert", "updated_at": _iso(),
            "payload": {
                "syncId": "tx-3", "type": "transfer", "amount": 1000,
                "happenedAt": "2026-04-16T02:00:00Z",
                "note": "还信用卡",
                "fromAccountName": "余额宝", "fromAccountId": "acc-savings",
                "toAccountName": "招商信用卡", "toAccountId": "acc-credit",
            },
        },
    ])


def _two_tokens(client, email):
    """注册一个用户,拿 app 和 web 两种 client_type 的 token。
    app token 用来 push 数据,web token 用来调读端点(/read/* 要 SCOPE_WEB_READ)。
    """
    app_token = _register_and_token(client, email, device_id="m-app", client_type="app")
    web_token = _register_and_token(client, email, device_id="m-web", client_type="web")
    return app_token, web_token


def _split_lines(body: str) -> list[str]:
    return [l for l in body.lstrip("\ufeff").split("\n") if l]


# ---------------------------------------------------------------------------
# 1. 表头 + BOM(默认 en + ?lang=zh-CN)
# ---------------------------------------------------------------------------


def test_csv_header_and_bom_default_en():
    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csv1@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _seed_basic(client, hdr, "m-app", "lg-1")

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-1"},
            headers=web_hdr,
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv")
        body = r.content.decode("utf-8")
        assert body.startswith("\ufeff"), "CSV must start with UTF-8 BOM"
        first_line = body.lstrip("\ufeff").split("\n")[0]
        # 默认 en
        assert first_line == (
            "Type,Category,Subcategory,Amount,Account,From Account,"
            "To Account,Note,Time,Tags,Attachments"
        )
    finally:
        app.dependency_overrides.clear()


def test_csv_header_localized_zh_cn():
    """?lang=zh-CN → 表头中文(跟 mobile 一致)。"""
    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csv1z@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _seed_basic(client, hdr, "m-app", "lg-1z")

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-1z", "lang": "zh-CN"},
            headers=web_hdr,
        )
        first_line = r.content.decode("utf-8").lstrip("\ufeff").split("\n")[0]
        assert first_line == "类型,分类,二级分类,金额,账户,转出账户,转入账户,备注,时间,标签,附件"
    finally:
        app.dependency_overrides.clear()


def test_csv_header_localized_zh_tw():
    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csv1t@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _seed_basic(client, hdr, "m-app", "lg-1t")

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-1t", "lang": "zh-TW"},
            headers=web_hdr,
        )
        first_line = r.content.decode("utf-8").lstrip("\ufeff").split("\n")[0]
        assert first_line == "類型,分類,二級分類,金額,帳戶,轉出帳戶,轉入帳戶,備註,時間,標籤,附件"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 2. Type 列本地化
# ---------------------------------------------------------------------------


def test_csv_type_label_zh_cn():
    """zh-CN:Type 列应是 收入/支出/转账,而不是 income/expense/transfer。"""
    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csv2@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _seed_basic(client, hdr, "m-app", "lg-2")

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-2", "lang": "zh-CN"},
            headers=web_hdr,
        )
        body = r.content.decode("utf-8").lstrip("\ufeff")
        # data 行(skip header)
        data_lines = body.split("\n", 1)[1]
        assert "收入" in data_lines
        assert "支出" in data_lines
        assert "转账" in data_lines
        # 不应再出现英文枚举(可能在 note 里出现 — 但 seed 数据没有,所以可断)
        assert "expense," not in data_lines
        assert "income," not in data_lines
        assert "transfer," not in data_lines
    finally:
        app.dependency_overrides.clear()


def test_csv_type_label_en():
    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csv2e@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _seed_basic(client, hdr, "m-app", "lg-2e")

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-2e", "lang": "en"},
            headers=web_hdr,
        )
        body = r.content.decode("utf-8").lstrip("\ufeff")
        data = body.split("\n", 1)[1]
        # 行首是 Type 列
        for prefix in ("Income,", "Expense,", "Transfer,"):
            assert prefix in data, f"missing {prefix} in:\n{data}"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 3. Sub-category 拆列
# ---------------------------------------------------------------------------


def test_csv_subcategory_split_for_level2():
    """tx-1 的分类是 午餐(level=2 parent=餐饮)→ Category=餐饮,SubCategory=午餐。"""
    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csv3@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _seed_basic(client, hdr, "m-app", "lg-3")

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-3", "lang": "zh-CN"},
            headers=web_hdr,
        )
        body = r.content.decode("utf-8").lstrip("\ufeff")
        # 找 expense 行(午餐 35.50)
        expense_line = next(
            (l for l in body.split("\n") if l.startswith("支出,")),
            None,
        )
        assert expense_line is not None, body
        cells = expense_line.split(",")
        # cells[0]=Type cells[1]=Category cells[2]=SubCategory cells[3]=Amount
        assert cells[1] == "餐饮", f"Category 应是 父类餐饮: {cells}"
        assert cells[2] == "午餐", f"SubCategory 应是 子类午餐: {cells}"
    finally:
        app.dependency_overrides.clear()


def test_csv_subcategory_empty_for_level1():
    """income 用的一级分类 工资 → Category=工资,SubCategory 空。"""
    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csv3a@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _seed_basic(client, hdr, "m-app", "lg-3a")

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-3a", "lang": "zh-CN"},
            headers=web_hdr,
        )
        body = r.content.decode("utf-8").lstrip("\ufeff")
        income_line = next(
            (l for l in body.split("\n") if l.startswith("收入,")),
            None,
        )
        assert income_line is not None, body
        cells = income_line.split(",")
        assert cells[1] == "工资"
        assert cells[2] == ""  # SubCategory 空
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 4. transfer 行:Account/Category/SubCategory 空,FromAccount/ToAccount 填
# ---------------------------------------------------------------------------


def test_csv_transfer_columns():
    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csv4@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _seed_basic(client, hdr, "m-app", "lg-4")

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-4", "lang": "zh-CN"},
            headers=web_hdr,
        )
        body = r.content.decode("utf-8").lstrip("\ufeff")
        transfer_line = next(
            (l for l in body.split("\n") if l.startswith("转账,")),
            None,
        )
        assert transfer_line is not None, body
        cells = transfer_line.split(",")
        # Type, Category, SubCategory, Amount, Account, FromAccount, ToAccount
        assert cells[0] == "转账"
        assert cells[1] == ""  # Category 空
        assert cells[2] == ""  # SubCategory 空
        assert cells[4] == ""  # Account 空
        assert cells[5] == "余额宝"  # FromAccount
        assert cells[6] == "招商信用卡"  # ToAccount
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 5. 转义 — note 含 , 和 "
# ---------------------------------------------------------------------------


def test_csv_escape_special_chars():
    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csv5@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _push(client, hdr, "m-app", [
            {
                "ledger_id": "lg-5e", "entity_type": "ledger", "entity_sync_id": "lg-5e",
                "action": "upsert", "updated_at": _iso(),
                "payload": {"syncId": "lg-5e", "ledgerName": "测试", "currency": "CNY"},
            },
            {
                "ledger_id": "lg-5e", "entity_type": "account", "entity_sync_id": "a1",
                "action": "upsert", "updated_at": _iso(),
                "payload": {"syncId": "a1", "name": "现金", "type": "cash", "currency": "CNY"},
            },
            {
                "ledger_id": "lg-5e", "entity_type": "category", "entity_sync_id": "c1",
                "action": "upsert", "updated_at": _iso(),
                "payload": {"syncId": "c1", "categoryName": "其他", "kind": "expense", "level": 1},
            },
            {
                "ledger_id": "lg-5e", "entity_type": "transaction", "entity_sync_id": "tx-esc",
                "action": "upsert", "updated_at": _iso(),
                "payload": {
                    "syncId": "tx-esc", "type": "expense", "amount": 12,
                    "happenedAt": _iso(),
                    "note": '他说"好吃",还想再来',
                    "categoryName": "其他", "categoryId": "c1",
                    "accountName": "现金", "accountId": "a1",
                },
            },
        ])

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-5e", "lang": "zh-CN"},
            headers=web_hdr,
        )
        body = r.content.decode("utf-8")
        assert '"他说""好吃"",还想再来"' in body, body
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 6. 中文文件名 RFC 5987
# ---------------------------------------------------------------------------


def test_csv_filename_chinese_rfc5987():
    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csv6@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _seed_basic(client, hdr, "m-app", "lg-6", ledger_name="个人账本")

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-6"},
            headers=web_hdr,
        )
        disp = r.headers.get("content-disposition", "")
        assert "filename*=UTF-8''" in disp
        assert "%E4" in disp or "%E5" in disp
        assert 'filename="' in disp
    finally:
        app.dependency_overrides.clear()


def test_csv_filename_no_dates_uses_timestamp():
    """没传 date_from / date_to → filename 用导出时间戳,不再是 'all_all'。
    避免多次下载同名 → OS 加 (1) (2) 后缀,看不出哪个是哪个。"""
    import re

    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csvf@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _seed_basic(client, hdr, "m-app", "lg-fn", ledger_name="个人账本")

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-fn"},
            headers=web_hdr,
        )
        disp = r.headers.get("content-disposition", "")
        # filename 段应含 YYYYMMDD-HHMMSS(导出时刻),不再有 all_all
        assert re.search(r"\d{8}-\d{6}", disp), disp
        assert "all_all" not in disp
    finally:
        app.dependency_overrides.clear()


def test_csv_filename_with_period_uses_dates():
    """传了 date_from / date_to → filename 用日期 period,不带时间戳。"""
    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csvp@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _seed_basic(client, hdr, "m-app", "lg-fp", ledger_name="个人账本")

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={
                "ledger_id": "lg-fp",
                "date_from": "2026-04-01T00:00:00Z",
                "date_to": "2026-05-01T00:00:00Z",
            },
            headers=web_hdr,
        )
        disp = r.headers.get("content-disposition", "")
        assert "2026-04-01_2026-05-01" in disp, disp
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 7. tz_offset_minutes 折算
# ---------------------------------------------------------------------------


def test_csv_tz_offset_local_time():
    """tz=480(CST):UTC 4/15 17:00 → 本地 4/16 01:00,Time 列含本地时间字符串。"""
    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csv7@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _push(client, hdr, "m-app", [
            {
                "ledger_id": "lg-7", "entity_type": "ledger", "entity_sync_id": "lg-7",
                "action": "upsert", "updated_at": _iso(),
                "payload": {"syncId": "lg-7", "ledgerName": "T", "currency": "CNY"},
            },
            {
                "ledger_id": "lg-7", "entity_type": "account", "entity_sync_id": "a1",
                "action": "upsert", "updated_at": _iso(),
                "payload": {"syncId": "a1", "name": "现金", "type": "cash", "currency": "CNY"},
            },
            {
                "ledger_id": "lg-7", "entity_type": "category", "entity_sync_id": "c1",
                "action": "upsert", "updated_at": _iso(),
                "payload": {"syncId": "c1", "categoryName": "x", "kind": "expense", "level": 1},
            },
            {
                "ledger_id": "lg-7", "entity_type": "transaction", "entity_sync_id": "tx-tz",
                "action": "upsert", "updated_at": _iso(),
                "payload": {
                    "syncId": "tx-tz", "type": "expense", "amount": 1,
                    "happenedAt": "2026-04-15T17:00:00Z",
                    "categoryName": "x", "categoryId": "c1",
                    "accountName": "现金", "accountId": "a1",
                },
            },
        ])

        # 不传 tz_offset → UTC,Time 列含 2026-04-15 17:00:00
        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-7"},
            headers=web_hdr,
        )
        body = r.content.decode("utf-8").lstrip("\ufeff")
        # Time 列是 "  YYYY-MM-DD HH:MM:SS  "(前后各 2 空格,跟 mobile 一致)。
        # 空格不触发 CSV 引号包裹(跟 dart ListToCsvConverter 默认行为一致)。
        assert "  2026-04-15 17:00:00  " in body, body

        # 传 tz_offset=480 → CST,Time 列含 2026-04-16 01:00:00
        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-7", "tz_offset_minutes": 480},
            headers=web_hdr,
        )
        body = r.content.decode("utf-8").lstrip("\ufeff")
        assert "  2026-04-16 01:00:00  " in body, body
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 8. filter passthrough
# ---------------------------------------------------------------------------


def test_csv_filter_passthrough_tx_type():
    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csv8@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _seed_basic(client, hdr, "m-app", "lg-8")

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-8", "tx_type": "income", "lang": "zh-CN"},
            headers=web_hdr,
        )
        body = r.content.decode("utf-8").lstrip("\ufeff")
        lines = [l for l in body.split("\n") if l]
        assert len(lines) == 2, f"income only,header + 1 行,实际 {len(lines)} 行:\n{body}"
        # data 行 Type 列应是 收入
        assert lines[1].startswith("收入,")
    finally:
        app.dependency_overrides.clear()


def test_csv_filter_passthrough_q():
    """q=工资 应只匹配 income 行(note='工资' / categoryName='工资')。"""
    client = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "csv8q@test.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}
        _seed_basic(client, hdr, "m-app", "lg-8q")

        r = client.get(
            "/api/v1/read/workspace/transactions.csv",
            params={"ledger_id": "lg-8q", "q": "工资", "lang": "zh-CN"},
            headers=web_hdr,
        )
        body = r.content.decode("utf-8").lstrip("\ufeff")
        lines = [l for l in body.split("\n") if l]
        assert len(lines) == 2, f"q=工资 应 1 行匹配:\n{body}"
        assert "工资" in lines[1]
    finally:
        app.dependency_overrides.clear()
