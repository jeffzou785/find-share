"""NeglectEvidenceCollector 测试（P1.5-3）。

只测纯函数和可 monkeypatch 的 collector 逻辑，不做真实网络请求。
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.collectors.neglect_evidence import (
    NeglectEvidenceCollector,
    _extract_stock_codes_from_obj,
    _parse_json_payload,
    period_return,
    relative_return,
)


class TestHotReasonParsing:
    def test_parse_jsonp_payload(self):
        payload = _parse_json_payload('callback({"data":[{"code":"600031"}]})')
        assert payload["data"][0]["code"] == "600031"

    def test_extract_codes_from_nested_payload(self):
        payload = {
            "data": [
                {"code": "600031", "name": "A"},
                {"股票代码": "000333.SZ"},
                {"items": [{"stock_code": "300750"}]},
                "002475",
            ]
        }
        assert _extract_stock_codes_from_obj(payload) == {
            "600031", "000333", "300750", "002475",
        }

    def test_hot_reason_count_uses_date_cache_loader(self, monkeypatch):
        c = NeglectEvidenceCollector(hot_lookback_days=3)
        calls = []

        def fake_loader(date_str):
            calls.append(date_str)
            if len(calls) == 1:
                return {"600031", "000333"}
            if len(calls) == 2:
                return {"600031"}
            return set()

        monkeypatch.setattr(c, "_load_hot_codes_for_date", fake_loader)
        assert c.get_hot_reason_count_30d("600031") == 2
        assert len(calls) == 3


class TestRelativeReturn:
    def test_period_return_uses_recent_window(self):
        df = pd.DataFrame(
            {
                "日期": pd.date_range("2026-01-01", periods=4),
                "收盘": [10.0, 11.0, 12.0, 15.0],
            }
        )
        assert period_return(df, lookback_days=2) == pytest.approx(15.0 / 11.0 - 1)

    def test_period_return_handles_empty_or_bad_data(self):
        assert period_return(pd.DataFrame(), lookback_days=2) is None
        assert period_return(pd.DataFrame({"日期": ["2026-01-01"], "收盘": [10]})) is None

    def test_relative_return_subtracts_benchmark(self):
        stock = pd.DataFrame(
            {"date": pd.date_range("2026-01-01", periods=3), "close": [10, 11, 12]}
        )
        benchmark = pd.DataFrame(
            {"date": pd.date_range("2026-01-01", periods=3), "close": [10, 10, 11]}
        )
        assert relative_return(stock, benchmark, lookback_days=2) == pytest.approx(0.1)


class TestComputeNeglectEvidence:
    def setup_method(self):
        self.c = NeglectEvidenceCollector()

    def test_all_none_returns_none(self):
        assert self.c.compute_neglect_evidence() is None

    def test_low_report_coverage(self):
        s = self.c.compute_neglect_evidence(reports_count_90d=2)
        assert "近 90 天仅 2 篇研报" in s

    def test_high_report_coverage(self):
        s = self.c.compute_neglect_evidence(reports_count_90d=10)
        assert "近 90 天 10 篇研报" in s
        assert "仅" not in s

    def test_low_news_count(self):
        s = self.c.compute_neglect_evidence(news_count_30d=3)
        assert "近 30 天仅 3 条新闻" in s

    def test_not_ai_related(self):
        s = self.c.compute_neglect_evidence(is_ai_related=False)
        assert "非 AI/半导体/机器人概念" in s

    def test_ai_related_warning(self):
        s = self.c.compute_neglect_evidence(is_ai_related=True)
        assert "不够被忽视" in s

    def test_relative_return_underperform(self):
        s = self.c.compute_neglect_evidence(relative_return_60d=-0.15)
        assert "跑输基准 15.0%" in s

    def test_relative_return_outperform(self):
        s = self.c.compute_neglect_evidence(relative_return_60d=0.08)
        assert "跑赢基准 8.0%" in s

    def test_aggregate_evidence(self):
        """多个信号合并：低研报 + 低新闻 + 非 AI + 跑输基准。"""
        s = self.c.compute_neglect_evidence(
            reports_count_90d=1, news_count_30d=2,
            is_ai_related=False, relative_return_60d=-0.12,
        )
        assert "近 90 天仅 1 篇研报" in s
        assert "近 30 天仅 2 条新闻" in s
        assert "非 AI/半导体/机器人概念" in s
        assert "跑输基准 12.0%" in s
        # 各部分用 "；" 分隔
        assert "；" in s
