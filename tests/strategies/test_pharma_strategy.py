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
