"""异常月份归因算法单测 —— _compute_anomaly_months。

锁定 .docs/dashboard-anomaly-budget/plan.md §2.1 里定的契约:
  - baseline = median(已发生月份)
  - 异常:expense > baseline × 1.2 AND expense - baseline > ¥200
  - 已发生月份 < 3 → 返回空
  - 归因:diff 最大的 top 2 category(本月独有时 multiplier=None)
"""
from __future__ import annotations

from src.routers.read.workspace import _compute_anomaly_months
from src.schemas import WorkspaceAnalyticsSeriesItemOut


def _series(items: list[tuple[str, float]]) -> list[WorkspaceAnalyticsSeriesItemOut]:
    """(bucket, expense) → SeriesItemOut。income/balance 不参与算法,填 0。"""
    return [
        WorkspaceAnalyticsSeriesItemOut(
            bucket=b, expense=exp, income=0.0, balance=-exp,
        )
        for b, exp in items
    ]


def test_less_than_3_months_returns_empty() -> None:
    """已发生月份 < 3 时 baseline 不稳,直接返回空。"""
    series = _series([("2026-01", 500.0), ("2026-02", 1000.0)])
    out = _compute_anomaly_months(series, {})
    assert out == []


def test_uniform_months_no_anomaly() -> None:
    """所有月份接近 baseline → 无异常(均没超 1.2× 阈值)。"""
    series = _series([
        ("2026-01", 800.0),
        ("2026-02", 850.0),
        ("2026-03", 820.0),
        ("2026-04", 900.0),
    ])
    cat = {
        b: {"餐饮": exp} for b, exp in [
            ("2026-01", 800.0), ("2026-02", 850.0),
            ("2026-03", 820.0), ("2026-04", 900.0),
        ]
    }
    out = _compute_anomaly_months(series, cat)
    assert out == []


def test_clear_anomaly_with_attribution() -> None:
    """一个月明显超出,归因到 diff 最大的分类。"""
    series = _series([
        ("2026-01", 800.0),
        ("2026-02", 850.0),
        ("2026-03", 820.0),
        ("2026-04", 800.0),
        ("2026-05", 1923.0),  # 异常:median 是 825,1923 > 825*1.2=990 且差 > 200
    ])
    cat = {
        "2026-01": {"餐饮": 600.0, "购物": 200.0},
        "2026-02": {"餐饮": 650.0, "购物": 200.0},
        "2026-03": {"餐饮": 620.0, "购物": 200.0},
        "2026-04": {"餐饮": 600.0, "购物": 200.0},
        # 5 月主要在购物上炸了
        "2026-05": {"餐饮": 700.0, "购物": 1000.0, "教育": 223.0},
    }
    out = _compute_anomaly_months(series, cat)
    assert len(out) == 1
    a = out[0]
    assert a.bucket == "2026-05"
    assert a.expense == 1923.0
    # baseline = median(800, 850, 820, 800, 1923) = 820
    assert a.baseline == 820.0
    assert a.deviation_pct > 1.3
    # 归因 top 2:购物 diff=800、教育 diff=223
    cats = [att.category_name for att in a.top_attributions]
    assert "购物" in cats
    # 教育是本月独有,median_others=0,multiplier=None
    edu = next((att for att in a.top_attributions if att.category_name == "教育"), None)
    if edu is not None:
        assert edu.median_others == 0.0
        assert edu.multiplier is None


def test_threshold_boundary_mult_not_met() -> None:
    """expense - baseline > 200 但 expense <= baseline × 1.2 → 不算异常。"""
    series = _series([
        ("2026-01", 5000.0),
        ("2026-02", 5000.0),
        ("2026-03", 5000.0),
        # baseline = 5000;5800 - 5000 = 800 (绝对差够)
        # 但 5800 / 5000 = 1.16 < 1.2 → 不算异常
        ("2026-04", 5800.0),
    ])
    out = _compute_anomaly_months(series, {})
    assert out == []


def test_threshold_boundary_abs_not_met() -> None:
    """expense > baseline × 1.2 但 expense - baseline <= 200 → 不算异常。"""
    series = _series([
        ("2026-01", 100.0),
        ("2026-02", 100.0),
        ("2026-03", 100.0),
        # baseline = 100;180 / 100 = 1.8(超 1.2),但 180 - 100 = 80 ≤ 200 → 不算
        ("2026-04", 180.0),
    ])
    out = _compute_anomaly_months(series, {})
    assert out == []


def test_unique_category_marked_with_null_multiplier() -> None:
    """本月独有的 category(其他月份 0 出现)→ multiplier=None。"""
    series = _series([
        ("2026-01", 500.0),
        ("2026-02", 500.0),
        ("2026-03", 500.0),
        ("2026-04", 1500.0),  # 异常
    ])
    cat = {
        "2026-01": {"餐饮": 500.0},
        "2026-02": {"餐饮": 500.0},
        "2026-03": {"餐饮": 500.0},
        # 4 月独有"装修"分类
        "2026-04": {"餐饮": 500.0, "装修": 1000.0},
    }
    out = _compute_anomaly_months(series, cat)
    assert len(out) == 1
    decor = next(
        (att for att in out[0].top_attributions if att.category_name == "装修"),
        None,
    )
    assert decor is not None
    assert decor.amount == 1000.0
    assert decor.median_others == 0.0
    assert decor.multiplier is None


def test_multiple_anomalies_sorted_by_deviation() -> None:
    """多个异常月份按 (expense - baseline) 降序。"""
    series = _series([
        ("2026-01", 500.0),
        ("2026-02", 500.0),
        ("2026-03", 500.0),
        ("2026-04", 1200.0),  # 异常 1,超 700
        ("2026-05", 2000.0),  # 异常 2,超 1500(更严重)
    ])
    cat = {b: {"X": exp} for b, exp in [
        ("2026-01", 500.0), ("2026-02", 500.0), ("2026-03", 500.0),
        ("2026-04", 1200.0), ("2026-05", 2000.0),
    ]}
    out = _compute_anomaly_months(series, cat)
    assert len(out) == 2
    # 严重的在前
    assert out[0].bucket == "2026-05"
    assert out[1].bucket == "2026-04"


def test_zero_expense_months_excluded_from_baseline() -> None:
    """expense=0 的"未发生月"不参与 baseline 计算。
    occurred = {01,03,05,06} = [800, 800, 850, 1500],median = 825
    (注意:6 月本身也算 occurred,但 median 不会被 0 月稀释成 0)
    """
    series = _series([
        ("2026-01", 800.0),
        ("2026-02", 0.0),  # 未发生,跳过
        ("2026-03", 800.0),
        ("2026-04", 0.0),  # 未发生
        ("2026-05", 850.0),
        ("2026-06", 1500.0),  # 异常
    ])
    cat = {b: {"X": exp} for b, exp in [
        ("2026-01", 800.0), ("2026-03", 800.0), ("2026-05", 850.0),
        ("2026-06", 1500.0),
    ]}
    out = _compute_anomaly_months(series, cat)
    assert len(out) == 1
    assert out[0].bucket == "2026-06"
    # median([800, 800, 850, 1500]) = (800+850)/2 = 825
    assert out[0].baseline == 825.0
