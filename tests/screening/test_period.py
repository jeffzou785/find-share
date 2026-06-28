"""P2-2 报告期解析工具测试。"""
from __future__ import annotations

import pytest

from src.screening.period import (
    KIND_ANNUAL,
    KIND_HALF_YEAR,
    KIND_Q1,
    KIND_Q3,
    PeriodInfo,
    parse_period,
    require_overseas_filter,
)


class TestParsePeriod:
    def test_annual(self):
        info = parse_period("2025A")
        assert info is not None
        assert info.kind == KIND_ANNUAL
        assert info.year == 2025
        assert info.suffix == "A"
        assert info.is_annual is True
        assert info.has_overseas_notes is True

    def test_half_year(self):
        info = parse_period("2025H")
        assert info is not None
        assert info.kind == KIND_HALF_YEAR
        assert info.is_annual is False
        assert info.has_overseas_notes is True

    def test_q1(self):
        info = parse_period("2025Q1")
        assert info is not None
        assert info.kind == KIND_Q1
        assert info.has_overseas_notes is False

    def test_q3(self):
        info = parse_period("2025Q3")
        assert info is not None
        assert info.kind == KIND_Q3
        assert info.has_overseas_notes is False

    def test_case_insensitive(self):
        assert parse_period("2025a").kind == KIND_ANNUAL
        assert parse_period("2025h").kind == KIND_HALF_YEAR
        assert parse_period("2025q1").kind == KIND_Q1

    def test_invalid_returns_none(self):
        assert parse_period("") is None
        assert parse_period("abc") is None
        assert parse_period("2025") is None
        assert parse_period("2025B") is None  # 不支持半年报以外的字母
        assert parse_period(None) is None

    def test_period_info_is_hashable(self):
        """PeriodInfo 是 frozen dataclass，应该可哈希。"""
        info = parse_period("2025A")
        assert info is not None
        d = {info: "test"}
        assert d[info] == "test"


class TestRequireOverseasFilter:
    def test_annual_requires(self):
        assert require_overseas_filter("2025A") is True

    def test_half_year_requires(self):
        assert require_overseas_filter("2025H") is True

    def test_q1_skips(self):
        """季报没有完整分地区附注，不强求海外过滤。"""
        assert require_overseas_filter("2025Q1") is False

    def test_q3_skips(self):
        assert require_overseas_filter("2025Q3") is False

    def test_unknown_period_conservative(self):
        """无法识别的 period 保守返回 True（沿用旧行为）。"""
        assert require_overseas_filter("") is True
        assert require_overseas_filter("garbage") is True
