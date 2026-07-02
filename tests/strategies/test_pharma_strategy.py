"""策略二医药行业池规则测试。"""
from __future__ import annotations

from src.strategies.pharma_strategy import (
    PHARMA_GROUND_TRUTH_COLUMNS,
    classify_pharma_sub_strategy,
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
