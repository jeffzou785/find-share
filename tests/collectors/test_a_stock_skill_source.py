"""$a-stock-data 口径 A 股数据源适配器测试。"""
from __future__ import annotations

import pandas as pd
import pytest

from src.collectors.a_stock_skill_source import (
    AStockSkillSource,
    financials_full_to_abstract,
    tencent_quote_one,
)
from src.collectors.sina_impl import ITEM_CN_MAP


class _FakeResponse:
    def __init__(self, text: str):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._text.encode("gbk")


def test_tencent_quote_one_parses_pe_pb(monkeypatch):
    fields = [""] * 60
    fields[1] = "测试股份"
    fields[3] = "12.34"
    fields[39] = "18.5"
    fields[44] = "123.0"
    fields[45] = "100.0"
    fields[46] = "2.1"
    fields[52] = "20.0"
    text = 'v_sh600001="' + "~".join(fields) + '";'

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=10: _FakeResponse(text),
    )

    q = tencent_quote_one("600001")
    assert q["name"] == "测试股份"
    assert q["price"] == pytest.approx(12.34)
    assert q["pe_ttm"] == pytest.approx(18.5)
    assert q["pb"] == pytest.approx(2.1)
    assert q["mcap_yi"] == pytest.approx(123.0)


def test_financials_full_to_abstract_derives_core_fields():
    full = pd.DataFrame([
        {"report_date": "2026-03-31", "item_en": "revenue", "value": 1000.0, "value_yoy": 25.0},
        {"report_date": "2026-03-31", "item_en": "operating_cost", "value": 600.0, "value_yoy": None},
        {"report_date": "2026-03-31", "item_en": "net_profit", "value": 120.0, "value_yoy": 30.0},
        {"report_date": "2026-03-31", "item_en": "net_profit_attr_parent", "value": 110.0, "value_yoy": 28.0},
        {"report_date": "2026-03-31", "item_en": "deducted_net_profit", "value": 90.0, "value_yoy": 35.0},
        {"report_date": "2026-03-31", "item_en": "accounts_receivable", "value": 120.0, "value_yoy": None},
        {"report_date": "2026-03-31", "item_en": "inventory", "value": 200.0, "value_yoy": None},
        {"report_date": "2026-03-31", "item_en": "selling_expense", "value": 80.0, "value_yoy": None},
        {"report_date": "2026-03-31", "item_en": "ocf_net", "value": 80.0, "value_yoy": None},
        {"report_date": "2026-03-31", "item_en": "share_capital", "value": 160.0, "value_yoy": None},
    ])

    abstract = financials_full_to_abstract(full)
    row = abstract.iloc[0]
    assert row["report_date"] == pd.Timestamp("2026-03-31")
    assert row["revenue"] == pytest.approx(1000.0)
    assert row["revenue_yoy"] == pytest.approx(25.0)
    assert row["gross_margin"] == pytest.approx(40.0)
    assert row["net_profit_attr_parent"] == pytest.approx(110.0)
    assert row["deducted_net_profit"] == pytest.approx(90.0)
    assert row["ocf_per_share"] == pytest.approx(0.5)
    assert row["accounts_receivable"] == pytest.approx(120.0)
    assert row["inventory"] == pytest.approx(200.0)
    assert row["selling_expense"] == pytest.approx(80.0)


def test_sina_parent_profit_aliases_map_to_same_field():
    assert ITEM_CN_MAP["归属于母公司股东的净利润"] == "net_profit_attr_parent"
    assert ITEM_CN_MAP["归属于母公司所有者的净利润"] == "net_profit_attr_parent"


def test_financials_full_to_abstract_does_not_use_total_cost_as_gross_margin():
    full = pd.DataFrame([
        {"report_date": "2026-03-31", "item_en": "revenue", "value": 1000.0, "value_yoy": None},
        {"report_date": "2026-03-31", "item_en": "total_operating_cost", "value": 900.0, "value_yoy": None},
    ])

    row = financials_full_to_abstract(full).iloc[0]

    assert pd.isna(row["gross_margin"])


def test_a_stock_skill_source_uses_recent_nine_periods_for_abstract():
    class MockSina:
        def __init__(self):
            self.num = None

        def get_all_statements(self, code: str, num: int = 8):
            self.num = num
            return pd.DataFrame([
                {"report_date": "2025-12-31", "item_en": "revenue", "value": 2000.0, "value_yoy": 10.0},
                {"report_date": "2025-12-31", "item_en": "gross_margin", "value": 30.0, "value_yoy": None},
                {"report_date": "2025-12-31", "item_en": "deducted_net_profit", "value": 180.0, "value_yoy": 40.0},
            ])

    mock = MockSina()
    source = AStockSkillSource(sina_source=mock)
    abstract = source.get_financial_abstract("000001")
    assert len(abstract) == 1
    assert mock.num == 9
    assert abstract.iloc[0]["revenue"] == pytest.approx(2000.0)
    assert abstract.iloc[0]["gross_margin"] == pytest.approx(30.0)
    assert abstract.iloc[0]["deducted_net_profit"] == pytest.approx(180.0)
