"""#101:父子分类的 parent_sync_id 稳定 FK 契约。

历史 bug:父分类改名从未级联子分类的 parent_name,而 Web 全链路(分组 /
has-children 守卫)按 parent_name 匹配 —— 改名后子分类从所有视图消失,
且守卫失明导致删父成功、子分类永久孤儿。

修复面(全部在本仓,web write 与 mobile push 两条路径汇入同一批函数):
  - projection.rename_cascade_category:按 FK 刷子行 parent_name + 老数据按名兜底
  - projection.upsert_category:payload 带悬空旧名时保留有效 FK(不许抹成 NULL)
  - sync_applier._USER_MERGE_SPECS["category"]:parentSyncId 进 merge 契约
    (漏登记 = mobile push 的 FK 通道死路 —— 2026-04 同类 bug)
  - snapshot_mutator:create/update 的 parentSyncId 透传 + 删父守卫双条件
  - snapshot_builder:sync/full 输出 parentSyncId(mobile 重装后 FK 不丢)
  - 读端点输出 parentSyncId(Web 前端改按 FK 分组)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import UserCategoryProjection


def _make_client() -> tuple[TestClient, sessionmaker]:
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


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_ledger(client: TestClient, token: str, device_id: str, ledger_id: str) -> None:
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
                    "updated_at": _iso(),
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _latest_change_id(client: TestClient, token: str, ledger_id: str) -> int:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    return int(res.json()["source_change_id"])


def _create_category(
    client: TestClient,
    token: str,
    ledger_id: str,
    *,
    name: str,
    kind: str = "expense",
    level: int = 1,
    parent_name: str | None = None,
) -> str:
    payload: dict = {
        "base_change_id": _latest_change_id(client, token, ledger_id),
        "name": name,
        "kind": kind,
        "level": level,
        "icon": "category",
        "icon_type": "material",
    }
    if parent_name:
        payload["parent_name"] = parent_name
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/categories",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert res.status_code == 200, res.text
    listing = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/categories",
        headers={"Authorization": f"Bearer {token}"},
    )
    cats = listing.json()
    matched = next((c for c in cats if c["name"] == name and c["kind"] == kind), None)
    assert matched, f"created category not found: {name}"
    return matched["id"]


def _rename_category(
    client: TestClient,
    token: str,
    ledger_id: str,
    category_id: str,
    *,
    name: str | None = None,
    parent_name: str | None = None,
) -> None:
    body: dict = {"base_change_id": _latest_change_id(client, token, ledger_id)}
    if name is not None:
        body["name"] = name
    if parent_name is not None:
        body["parent_name"] = parent_name
    res = client.patch(
        f"/api/v1/write/ledgers/{ledger_id}/categories/{category_id}",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    assert res.status_code == 200, res.text


def _delete_category(
    client: TestClient, token: str, ledger_id: str, category_id: str
) -> tuple[int, str]:
    res = client.request(
        "DELETE",
        f"/api/v1/write/ledgers/{ledger_id}/categories/{category_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_change_id": _latest_change_id(client, token, ledger_id)},
    )
    return res.status_code, res.text


def _push_category(
    client: TestClient, token: str, sync_id: str, payload: dict, *, device_id: str
) -> None:
    body = dict(payload)
    body.setdefault("syncId", sync_id)
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": "lg-any",
                    "entity_type": "category",
                    "entity_sync_id": sync_id,
                    "action": "upsert",
                    "payload": body,
                    "updated_at": _iso(),
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _projection_row(TS: sessionmaker, sync_id: str) -> UserCategoryProjection:
    with TS() as db:
        row = db.scalar(
            select(UserCategoryProjection).where(
                UserCategoryProjection.sync_id == sync_id
            )
        )
        assert row is not None, f"projection row missing: {sync_id}"
        db.expunge(row)
        return row


# --------------------------------------------------------------------------- #
# Web write 路径                                                                #
# --------------------------------------------------------------------------- #


def test_web_rename_parent_cascades_children_parent_sync_id() -> None:
    """Web 改父分类名:子分类 parent_name 跟着刷 + parent_sync_id 保持稳定,
    读端点暴露 parent_sync_id,删父守卫(按 FK 计数)依然拦截。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "cat101-web@example.com")
        ledger_id = "L_CAT101_WEB"
        _seed_ledger(client, owner["access_token"], owner["device_id"], ledger_id)

        web = _login_web(client, "cat101-web@example.com")
        token = web["access_token"]

        parent_id = _create_category(client, token, ledger_id, name="餐饮", level=1)
        child_id = _create_category(
            client, token, ledger_id, name="早餐", level=2, parent_name="餐饮"
        )

        # 新建后读端点就应暴露 parent_sync_id(按名解析兜底)
        cats = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/categories",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        child = next(c for c in cats if c["id"] == child_id)
        assert child["parent_sync_id"] == parent_id, child
        assert child["parent_name"] == "餐饮"

        # 改名 → 子行 parent_name 级联 + FK 不变
        _rename_category(client, token, ledger_id, parent_id, name="吃饭")
        cats = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/categories",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        child = next(c for c in cats if c["id"] == child_id)
        assert child["parent_name"] == "吃饭", f"#101 dangling name: {child}"
        assert child["parent_sync_id"] == parent_id

        # 删父守卫:子行还在 → 拒删
        status, text = _delete_category(client, token, ledger_id, parent_id)
        assert status >= 400, f"expected has-children reject, got {status}: {text}"
    finally:
        app.dependency_overrides.clear()


def test_web_sync_full_snapshot_emits_parent_sync_id() -> None:
    """/sync/full 懒构建的 snapshot 必须带 parentSyncId —— 否则 mobile 重装
    全量同步后 FK 丢失,回到按名匹配的老世界。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "cat101-snap@example.com")
        ledger_id = "L_CAT101_SNAP"
        _seed_ledger(client, owner["access_token"], owner["device_id"], ledger_id)

        web = _login_web(client, "cat101-snap@example.com")
        token = web["access_token"]

        parent_id = _create_category(client, token, ledger_id, name="购物", level=1)
        _create_category(
            client, token, ledger_id, name="衣服", level=2, parent_name="购物"
        )

        res = client.get(
            f"/api/v1/sync/full?ledger_id={ledger_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        snap = res.json()
        # 响应形状:{snapshot: {payload: {content: "<snapshot json string>"}}}
        content = json.loads(snap["snapshot"]["payload"]["content"])
        by_name = {c["name"]: c for c in content["categories"]}
        assert "衣服" in by_name
        assert by_name["衣服"].get("parentSyncId") == parent_id, by_name["衣服"]
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Mobile push 路径                                                              #
# --------------------------------------------------------------------------- #


def test_mobile_rename_parent_cascades_children_and_heals_legacy_rows() -> None:
    """mobile push 改父分类名(rename cascade 在 push 路径汇入同一函数):
    子行 parent_name 级联,FK 不变;老数据行(无 FK)按 (旧名, kind) 兜底
    级联并顺手补上 FK(自愈)。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "cat101-mobile@example.com")
        token = owner["access_token"]

        # 造"老数据"子行:子先于父 push,parentName 当时解析不到 → FK 为 NULL
        _push_category(client, token, "cat101-child-old", {
            "name": "打车", "kind": "expense", "level": 2, "parentName": "出行",
        }, device_id=owner["device_id"])
        _push_category(client, token, "cat101-parent", {
            "name": "出行", "kind": "expense", "level": 1,
        }, device_id=owner["device_id"])
        # 新数据子行:带稳定 FK
        _push_category(client, token, "cat101-child-new", {
            "name": "地铁", "kind": "expense", "level": 2,
            "parentName": "出行", "parentSyncId": "cat101-parent",
        }, device_id=owner["device_id"])

        legacy = _projection_row(TS, "cat101-child-old")
        assert legacy.parent_sync_id is None
        assert legacy.parent_name == "出行"

        # 改父名
        _push_category(client, token, "cat101-parent", {
            "name": "通勤", "kind": "expense", "level": 1,
        }, device_id=owner["device_id"])

        new_child = _projection_row(TS, "cat101-child-new")
        assert new_child.parent_name == "通勤"
        assert new_child.parent_sync_id == "cat101-parent"

        legacy = _projection_row(TS, "cat101-child-old")
        assert legacy.parent_name == "通勤", f"#101 legacy dangling: {legacy.parent_name}"
        assert legacy.parent_sync_id == "cat101-parent", "兜底应顺手自愈 FK"
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_category_partial_update_keeps_parent_fields() -> None:
    """merge 契约(CLAUDE.md 约定风格):partial update 不带 parent 字段时,
    既有 parent_name / parent_sync_id 不丢;payload 显式带 parentSyncId 时
    必须落库(漏登记 merge spec = FK 通道死路)。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "cat101-merge@example.com")
        token = owner["access_token"]

        _push_category(client, token, "cat101-m-parent", {
            "name": "娱乐", "kind": "expense", "level": 1,
        }, device_id=owner["device_id"])
        _push_category(client, token, "cat101-m-child", {
            "name": "电影", "kind": "expense", "level": 2,
            "parentName": "娱乐", "parentSyncId": "cat101-m-parent",
        }, device_id=owner["device_id"])
        row = _projection_row(TS, "cat101-m-child")
        assert row.parent_sync_id == "cat101-m-parent"
        assert row.parent_name == "娱乐"

        # partial update:只补 icon,不带任何 parent 字段 → 两者都保留
        _push_category(client, token, "cat101-m-child", {
            "name": "电影", "kind": "expense", "level": 2, "icon": "movie",
        }, device_id=owner["device_id"])
        row = _projection_row(TS, "cat101-m-child")
        assert row.parent_sync_id == "cat101-m-parent", "partial update 丢 FK"
        assert row.parent_name == "娱乐", "partial update 丢 parent_name"

        # 父改名后,子行 push 带着改名前的 stale parentName(老客户端典型
        # payload):不许把有效 FK 抹成 NULL,parent_name 沿用库里的现值
        # (cascade 已刷成新名)
        _push_category(client, token, "cat101-m-parent", {
            "name": "休闲", "kind": "expense", "level": 1,
        }, device_id=owner["device_id"])
        _push_category(client, token, "cat101-m-child", {
            "name": "电影", "kind": "expense", "level": 2, "parentName": "娱乐",
        }, device_id=owner["device_id"])
        row = _projection_row(TS, "cat101-m-child")
        assert row.parent_sync_id == "cat101-m-parent", "stale name 抹掉了有效 FK"
        assert row.parent_name == "休闲"
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Web 删父守卫:FK 权威,parentName 悬空也不漏判                                   #
# --------------------------------------------------------------------------- #


def test_web_delete_guard_counts_child_by_parent_sync_id_alone() -> None:
    """子行 parent_name 被改坏(悬空)但 FK 有效时,删父守卫必须仍按 FK
    计数拦截 —— 双条件兜底正是为这种历史脏数据。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "cat101-guard@example.com")
        ledger_id = "L_CAT101_GUARD"
        _seed_ledger(client, owner["access_token"], owner["device_id"], ledger_id)

        web = _login_web(client, "cat101-guard@example.com")
        token = web["access_token"]

        parent_id = _create_category(client, token, ledger_id, name="医疗", level=1)
        child_id = _create_category(
            client, token, ledger_id, name="药品", level=2, parent_name="医疗"
        )

        # 把子行 parent_name 改成悬空名(mutator 透传),parentSyncId 不在
        # payload 里、保持原值 → FK 仍有效
        _rename_category(
            client, token, ledger_id, child_id, parent_name="不存在的父名"
        )

        status, text = _delete_category(client, token, ledger_id, parent_id)
        assert status >= 400, f"FK 有效时应拒删, got {status}: {text}"
        assert "child" in text.lower() or "subcateg" in text.lower() or "校验" in text
    finally:
        app.dependency_overrides.clear()
