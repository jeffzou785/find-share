"""F10 overseas revenue fallback parser tests."""
from __future__ import annotations

from src.collectors.f10_overseas_revenue import (
    parse_f10_overseas_records,
    parse_f10_overseas_revenue,
)


def test_parse_f10_overseas_records_with_unit_header():
    text = """
    主营构成（单位：万元）
    报告期：2025年报
    地区 主营收入 占比
    境内 120,000.00 60.00%
    境外 80,000.00 40.00%
    """
    records = parse_f10_overseas_records(text, stock_code="1311", report_year=2025)
    assert len(records) == 1
    record = records[0]
    assert record.stock_code == "001311"
    assert record.region_name == "境外"
    assert record.revenue == 80000.0
    assert record.revenue_unit == "万元"
    assert record.revenue_yuan == 80000.0 * 10000.0
    assert record.confidence == "high"


def test_parse_f10_filters_other_report_year():
    text = """
    2024年 主营构成 单位：万元
    境外 10,000.00
    2025年 主营构成 单位：万元
    境外 20,000.00
    """
    records = parse_f10_overseas_records(text, stock_code="002085", report_year=2025)
    assert len(records) == 1
    assert records[0].revenue == 20000.0


def test_parse_f10_ignores_export_volume_without_money_amount():
    text = "2025年 出口量最高可达1.2亿吨，国外销售金额占比43.57%。"
    records = parse_f10_overseas_records(text, stock_code="002145", report_year=2025)
    assert records == []


def test_parse_f10_overseas_revenue_result_shape():
    text = "2025年 主营构成 单位：亿元\n国外 12.5 38.0%"
    result = parse_f10_overseas_revenue(text, stock_code="2085", report_year=2025)
    assert result.success is True
    assert result.stock_code == "002085"
    assert result.pdf_path == "mootdx_f10:002085:2025"
    assert result.records[0].revenue_yuan == 12.5 * 100_000_000.0
    assert "f10_fallback_used" in result.parse_warnings


def test_parse_f10_empty_result_is_failure():
    result = parse_f10_overseas_revenue("没有海外收入", stock_code="002085", report_year=2025)
    assert result.success is False
    assert "未提取到境外收入" in result.error
