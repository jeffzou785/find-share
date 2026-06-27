"""策略一反转判定测试（P0-12）。

仅测纯函数 is_inflection / is_trend，不依赖数据源。
"""
from __future__ import annotations

import math

import pytest

from src.strategies.consumer_reversal import is_inflection, is_trend


class TestIsInflection:
    def test_classic_inflection(self):
        """当期 >=30% & 前期 <0% → True。"""
        assert is_inflection([-0.15, 0.35]) is True

    def test_prev_zero_excluded(self):
        """前期 =0 不满足严格 <0。"""
        assert is_inflection([0.0, 0.35]) is False

    def test_current_below_threshold(self):
        """当期 <30% 不命中拐点。"""
        assert is_inflection([-0.20, 0.25]) is False

    def test_prev_positive(self):
        """前期已经为正，不算"刚从负转正"。"""
        assert is_inflection([0.10, 0.40]) is False

    def test_insufficient_data(self):
        assert is_inflection([0.35]) is False
        assert is_inflection([]) is False

    def test_nan_safe(self):
        """含 NaN 的序列不应抛异常。"""
        assert is_inflection([float("nan"), 0.40]) is False
        assert is_inflection([-0.10, float("nan")]) is False


class TestIsTrend:
    def test_classic_trend(self):
        """当期>=20% & 前期>=20% & 前前期<0% → True。"""
        assert is_trend([-0.10, 0.22, 0.25]) is True

    def test_prev_below_threshold(self):
        assert is_trend([-0.10, 0.15, 0.25]) is False

    def test_prev_prev_zero_excluded(self):
        """前前期 =0 不算从负转正。"""
        assert is_trend([0.0, 0.22, 0.25]) is False

    def test_prev_prev_positive(self):
        assert is_trend([0.05, 0.22, 0.25]) is False

    def test_insufficient_data(self):
        assert is_trend([-0.10, 0.22]) is False
        assert is_trend([0.22]) is False

    def test_nan_safe(self):
        assert is_trend([-0.10, float("nan"), 0.25]) is False

    def test_long_series_uses_last_three(self):
        """超过 3 期时只看最近 3 期。"""
        assert is_trend([0.5, 0.5, -0.10, 0.22, 0.25]) is True
