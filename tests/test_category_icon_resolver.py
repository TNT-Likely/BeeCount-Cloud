"""byName 图标解析单测 —— 锁定 Flutter getCategoryIconByName 的 1:1 行为。

Flutter 侧 `lib/services/data/category_service.dart:8-201` 原文规则迁移到 Python
后,必须保持**相同的匹配顺序 + 相同输出**。Flutter 代码或本模块 drift 时,这里
应能立即红灯。

目前只有 Python 侧生效(alembic 0002 backfill + sync push handler 兜底)。等
Flutter 3.0.1 做 write-time migration + 移除 getCategoryIconByName 调用后,两边
同步退役本模块。
"""

import pytest

from src.services.category_icon import (
    needs_backfill,
    resolve_category_icon,
    resolve_icon_by_name,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # 用户反馈的具体 case ——首屏必须对
        ("爱车养车", "directions_car"),
        ("家居家装", "chair"),
        ("母婴2", "child_friendly"),
        ("退款", "savings"),  # Flutter 故意把"退款"归到 savings,照搬
        # 常规规则
        ("餐饮", "restaurant"),
        ("外卖", "restaurant"),
        ("打车", "local_taxi"),
        ("地铁", "subway"),
        ("公交", "directions_bus"),
        ("高铁", "train"),
        ("火车", "train"),
        ("飞机", "flight"),
        ("出行", "directions_transit"),
        ("车", "directions_car"),
        ("购车", "directions_car"),
        ("淘宝购物", "shopping_bag"),
        ("聚会", "groups"),
        ("衣服", "checkroom"),
        ("超市", "local_grocery_store"),
        ("水果", "local_grocery_store"),
        ("电影", "sports_esports"),
        ("家庭", "family_restroom"),
        ("居家", "chair"),
        ("物业", "chair"),
        ("化妆", "brush"),
        ("话费", "network_cell"),
        ("订阅", "subscriptions"),
        ("红包", "card_giftcard"),
        ("水电", "water_drop"),  # 含"水"/"电"
        ("房贷", "account_balance"),
        ("工资", "attach_money"),
        ("基金", "savings"),
        ("教育", "menu_book"),
        ("医院", "medical_services"),
        ("宠物", "pets"),
        ("健身", "fitness_center"),
        ("手机", "devices_other"),
        ("旅游", "card_travel"),
        ("酒店", "hotel"),
        ("烟酒", "local_bar"),
        ("停车", "local_parking"),
        ("加油", "local_gas_station"),
        ("保养", "build"),
        ("过路费", "alt_route"),
        ("快递", "local_shipping"),
        ("社保", "receipt_long"),
        ("捐赠", "volunteer_activism"),
        ("办公", "work"),
        # 优先级 —— "聚会"规则(social/groups)排在"家庭"之前,所以"家庭聚会"
        # 其实走 groups,不是 family_restroom。这是 Flutter 原文的既定行为。
        ("家庭聚会", "groups"),
        ("妈妈家庭", "family_restroom"),  # 没"聚会"了,走"家庭" → family_restroom
        # "居家"命中"家"字,抢在"住房"前
        ("住房家居", "chair"),
        # 兜底
        ("", "circle"),
        ("不知道是啥", "circle"),
    ],
)
def test_by_name_rules(name: str, expected: str) -> None:
    assert resolve_icon_by_name(name) == expected


def test_resolve_category_icon_priority() -> None:
    """stored icon 非空时直接透传,不走 byName。"""
    assert resolve_category_icon("build", "爱车养车") == "build"
    assert resolve_category_icon("  custom  ", "爱车养车") == "custom"
    # 空 icon → 走 byName
    assert resolve_category_icon(None, "爱车养车") == "directions_car"
    assert resolve_category_icon("", "爱车养车") == "directions_car"
    assert resolve_category_icon("   ", "爱车养车") == "directions_car"
    # icon 和 name 都空 → circle
    assert resolve_category_icon(None, None) == "circle"
    assert resolve_category_icon("", "") == "circle"


def test_needs_backfill() -> None:
    """空字符串 / 纯空格 / None 都需要 backfill,非空(包括 'category')不需要。"""
    assert needs_backfill(None)
    assert needs_backfill("")
    assert needs_backfill("   ")
    # 'category' 是 category_edit_page 的默认初值,代表用户显式/默认接受,
    # 不视为需要覆盖
    assert not needs_backfill("category")
    assert not needs_backfill("build")
    assert not needs_backfill("restaurant")
