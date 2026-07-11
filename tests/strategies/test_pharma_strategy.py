"""策略二医药行业池规则测试。"""
from __future__ import annotations

import pandas as pd

from src.screening import Status
from src.strategies.pharma_strategy import (
    PHARMA_GROUND_TRUTH_COLUMNS,
    VbpRecoveryConfig,
    classify_pharma_sub_strategy,
    evaluate_vbp_recovery_batch,
    evaluate_vbp_recovery_one,
)


def test_classify_vbp_recovery_industry():
    result = classify_pharma_sub_strategy(
        sw_first="医药生物",
        sw_second="化学制剂",
    )
    assert result is not None
    assert result.sub_strategy == "vbp_recovery"
    assert result.matched_keyword == "化学制剂"


def test_classify_innovation_export_industry():
    result = classify_pharma_sub_strategy(
        sw_first="医药生物",
        sw_second="生物制品",
        business_tags="License-out FDA",
    )
    assert result is not None
    assert result.sub_strategy == "innovation_export"


def test_excludes_cxo_and_medical_service():
    assert classify_pharma_sub_strategy(
        sw_first="医药生物",
        sw_second="CXO",
    ) is None
    assert classify_pharma_sub_strategy(
        sw_first="医药生物",
        sw_second="医疗服务",
    ) is None


def test_non_pharma_returns_none():
    assert classify_pharma_sub_strategy(sw_first="食品饮料", sw_second="白酒") is None


def test_non_pharma_sw_first_is_not_pulled_in_by_business_tags():
    assert classify_pharma_sub_strategy(
        sw_first="机械设备",
        sw_second="专用设备",
        business_tags="FDA 海外临床",
    ) is None


def test_ground_truth_columns_include_required_label_fields():
    assert "human_label" in PHARMA_GROUND_TRUTH_COLUMNS
    assert "label_reason" in PHARMA_GROUND_TRUTH_COLUMNS
    assert "label_version" in PHARMA_GROUND_TRUTH_COLUMNS


def _candidate():
    return {
        "code": "600276",
        "name": "恒瑞医药",
        "sw_first": "医药生物",
        "sw_second": "化学制剂",
    }


def _financials(revenue_yoy=12.0, net_profit_yoy=8.0, gross_margin=30.0):
    return pd.DataFrame([
        {
            "report_date": "2025-03-31",
            "revenue_yoy": 1.0,
            "net_profit_yoy": -5.0,
            "deducted_net_profit": 100.0,
            "gross_margin": 29.0,
            "ocf_per_share": 0.1,
        },
        {
            "report_date": "2026-03-31",
            "revenue_yoy": revenue_yoy,
            "net_profit_yoy": net_profit_yoy,
            "deducted_net_profit": 120.0,
            "gross_margin": gross_margin,
            "ocf_per_share": 0.2,
        },
    ])


def _vbp_events(status="won"):
    return pd.DataFrame([
        {
            "code": "600276",
            "name": "恒瑞医药",
            "product_name": "药品A",
            "vbp_batch": "第八批",
            "vbp_status": status,
            "tender_date": "2025-01-01",
            "price_before": 10.0,
            "price_after": 4.0,
            "source_url": "https://example.com",
            "evidence_text": "中选",
        }
    ])


def _pe_pb_history(current_pb=1.0, previous_pb=1.0):
    dates = pd.date_range("2025-01-01", periods=60, freq="D")
    return pd.DataFrame({
        "date": dates,
        "pb": [previous_pb] * 59 + [current_pb],
        "pe_ttm": [20.0] * 60,
        "close": [10.0] * 60,
    })


def test_evaluate_vbp_recovery_hit_when_financials_recover():
    result = evaluate_vbp_recovery_one(
        candidate=_candidate(),
        financials=_financials(),
        vbp_events=_vbp_events(),
        run_id="r1",
        period="2026Q1",
    )
    assert result.status == Status.HIT
    assert result.hit_reason == "vbp_recovery_confirmed"
    assert result.metrics.growth.revenue_yoy == 0.12
    assert result.metrics.source_status.extra["vbp_status"] == "won"


def test_evaluate_vbp_recovery_accepts_decimal_yoy_from_a_stock_source():
    result = evaluate_vbp_recovery_one(
        candidate=_candidate(),
        financials=_financials(revenue_yoy=0.12, net_profit_yoy=0.08),
        vbp_events=_vbp_events(),
        run_id="r1",
        period="2026Q1",
    )
    assert result.status == Status.HIT
    assert result.metrics.growth.revenue_yoy == 0.12
    assert result.metrics.growth.net_profit_yoy == 0.08


def test_vbp_event_product_can_classify_broad_biomedicine_industry():
    """行业二级分类过粗时，VBP 事件 product_name 作为归类辅助证据。

    注：'生物医药' 自 Phase E 起直接命中 VBP_RECOVERY_KEYWORDS，所以这里
    用 '其他医药分类' 模拟无直接 keyword 命中的场景，验证 event-driven rescue。
    """
    candidate = {
        "code": "603658",
        "name": "安图生物",
        "sw_first": "医药生物",
        "sw_second": "其他医药分类",
    }
    events = pd.DataFrame([
        {
            "code": "603658",
            "name": "安图生物",
            "product_name": "体外诊断试剂",
            "vbp_batch": "IVD省际联盟集采",
            "vbp_status": "unknown",
            "tender_date": "2025-01-01",
            "source_url": "https://example.com",
            "evidence_text": "体外诊断试剂集采",
        }
    ])
    result = evaluate_vbp_recovery_one(
        candidate=candidate,
        financials=_financials(),
        vbp_events=events,
        run_id="r1",
        period="2026Q1",
    )
    assert result.status == Status.WATCH
    assert result.watch_reason == "vbp_status_unknown"
    assert result.metrics.source_status.extra["matched_keyword"] == "体外诊断"


def test_biomedicine_sw_second_directly_classified_as_vbp_recovery():
    """Phase E：sw_second='生物医药' 直接命中 VBP_RECOVERY_KEYWORDS。

    长春高新/复星医药/通化东宝 之前因 keyword 缺失被 not_vbp_recovery_pool reject，
    事件根本不会被读取。修复后由事件驱动细分。
    """
    candidate = {
        "code": "600867",
        "name": "通化东宝",
        "sw_first": "医药生物",
        "sw_second": "生物医药",
    }
    events = pd.DataFrame([
        {
            "code": "600867",
            "name": "通化东宝",
            "product_name": "胰岛素",
            "vbp_batch": "国家组织胰岛素专项集采",
            "vbp_status": "won",
            "tender_date": "2021-11-26",
            "source_url": "https://www.smpaa.cn/gjsdcg/2021/11/26/10397.shtml",
            "evidence_text": "胰岛素专项集采中选",
        }
    ])
    result = evaluate_vbp_recovery_one(
        candidate=candidate,
        financials=_financials(),
        vbp_events=events,
        run_id="r1",
        period="2026Q1",
    )
    # 应进入 vbp_recovery 流程（而非 not_vbp_recovery_pool）
    assert result.status != Status.REJECTED or result.reject_reason != "not_vbp_recovery_pool"
    assert result.metrics.source_status.extra["matched_keyword"] == "生物医药"


def test_evaluate_vbp_recovery_high_pb_percentile_downgrades_hit_to_watch():
    result = evaluate_vbp_recovery_one(
        candidate=_candidate(),
        financials=_financials(),
        vbp_events=_vbp_events(),
        pe_pb_history=_pe_pb_history(current_pb=10.0, previous_pb=1.0),
        run_id="r1",
        period="2026Q1",
    )
    assert result.status == Status.WATCH
    assert result.watch_reason == "pb_percentile_high_soft_constraint"
    assert result.metrics.valuation.pb == 10.0
    assert result.metrics.valuation.pb_pct_5y == 100.0


def test_evaluate_vbp_recovery_requires_event():
    result = evaluate_vbp_recovery_one(
        candidate=_candidate(),
        financials=_financials(),
        vbp_events=pd.DataFrame(),
        run_id="r1",
        period="2026Q1",
    )
    assert result.status == Status.DATA_MISSING
    assert result.data_missing_reason == "vbp_event_missing"


def test_evaluate_vbp_recovery_lost_status_only_watch_even_if_financials_recover():
    result = evaluate_vbp_recovery_one(
        candidate=_candidate(),
        financials=_financials(),
        vbp_events=_vbp_events(status="lost"),
        run_id="r1",
        period="2026Q1",
    )
    assert result.status == Status.WATCH
    assert result.watch_reason == "financial_recovery_but_vbp_status_lost"


def test_evaluate_vbp_recovery_unknown_status_keeps_metrics_for_labeling():
    result = evaluate_vbp_recovery_one(
        candidate=_candidate(),
        financials=_financials(),
        vbp_events=_vbp_events(status="unknown"),
        run_id="r1",
        period="2026Q1",
    )
    assert result.status == Status.WATCH
    assert result.watch_reason == "vbp_status_unknown"
    assert result.metrics.growth.revenue_yoy == 0.12
    assert result.metrics.source_status.extra["vbp_status"] == "unknown"


def test_evaluate_vbp_recovery_rejected_when_recovery_not_confirmed():
    result = evaluate_vbp_recovery_one(
        candidate=_candidate(),
        financials=_financials(revenue_yoy=-5.0, net_profit_yoy=-10.0, gross_margin=25.0),
        vbp_events=_vbp_events(),
        run_id="r1",
        period="2026Q1",
    )
    assert result.status == Status.REJECTED
    assert result.reject_reason == "vbp_recovery_not_confirmed"


def test_evaluate_vbp_recovery_batch_filters_to_code_events():
    candidates = pd.DataFrame([_candidate(), {**_candidate(), "code": "600519"}])
    results = evaluate_vbp_recovery_batch(
        candidates=candidates,
        financials_by_code={"600276": _financials(), "600519": _financials()},
        vbp_events=_vbp_events(),
        run_id="r1",
        period="2026Q1",
        config=VbpRecoveryConfig(),
    )
    assert [r.code for r in results] == ["600276", "600519"]
    assert results[0].status == Status.HIT
    assert results[1].status == Status.DATA_MISSING
