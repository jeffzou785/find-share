"""NeglectEvidenceCollector 测试（P1.5-3）。

只测聚合函数 `compute_neglect_evidence`（纯函数，无网络）。
网络 collector（get_news_count_30d / is_ai_related）不在单测范围。
"""
from __future__ import annotations

import pytest

from src.collectors.neglect_evidence import NeglectEvidenceCollector


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
        assert "跑输行业 15.0%" in s

    def test_relative_return_outperform(self):
        s = self.c.compute_neglect_evidence(relative_return_60d=0.08)
        assert "跑赢行业 8.0%" in s

    def test_aggregate_evidence(self):
        """多个信号合并：低研报 + 低新闻 + 非 AI + 跑输行业。"""
        s = self.c.compute_neglect_evidence(
            reports_count_90d=1, news_count_30d=2,
            is_ai_related=False, relative_return_60d=-0.12,
        )
        assert "近 90 天仅 1 篇研报" in s
        assert "近 30 天仅 2 条新闻" in s
        assert "非 AI/半导体/机器人概念" in s
        assert "跑输行业 12.0%" in s
        # 各部分用 "；" 分隔
        assert "；" in s
