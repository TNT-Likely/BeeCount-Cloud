"""归物成本口径:关联交易收入(income)同样抵减净成本。

对应「收入也可以关联归物」需求 —— 挂在物品上的收入交易(回收款 / 折价补偿 /
保修理赔入账等)与支出相反,应降低净成本。
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.entity_extensions import item_derived


def _d(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_linked_income_reduces_net_cost():
    # 购买 6000 + 支出 400 - 收入 500 = 5900
    derived = item_derived(
        purchase_date=_d("2026-01-01T00:00:00+00:00"),
        purchase_amount=6000.0,
        warranty_until=None,
        status="in_use",
        disposal_type=None,
        disposal_date=None,
        disposal_amount=None,
        linked_expense=400.0,
        linked_income=500.0,
    )
    assert derived["linked_expense"] == 400.0
    assert derived["linked_income"] == 500.0
    assert derived["total_cost"] == 5900.0


def test_linked_income_only():
    # 没有支出、只有收入:净成本被收入拉低(可为负)。
    derived = item_derived(
        purchase_date=_d("2026-01-01T00:00:00+00:00"),
        purchase_amount=1000.0,
        warranty_until=None,
        status="in_use",
        disposal_type=None,
        disposal_date=None,
        disposal_amount=None,
        linked_expense=0.0,
        linked_income=300.0,
    )
    assert derived["total_cost"] == 700.0


def test_income_plus_sale_disposal_both_reduce():
    # 收入关联交易 + 二手卖出回收款,两笔都抵减成本。
    derived = item_derived(
        purchase_date=_d("2026-01-01T00:00:00+00:00"),
        purchase_amount=6000.0,
        warranty_until=None,
        status="disposed",
        disposal_type="sold",
        disposal_date=_d("2026-06-01T00:00:00+00:00"),
        disposal_amount=2000.0,
        linked_expense=400.0,
        linked_income=500.0,
    )
    # 6000 + 400 - 500 - 2000 = 3900
    assert derived["total_cost"] == 3900.0
