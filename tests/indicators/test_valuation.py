"""Tests for src/indicators/valuation.py::compute_pe_pb_percentile.

Covers the negative-current-PE regression: when current PE ≤ 0 but historical
PE values are positive, the function must report current_valid=False and
percentile=None instead of using a stale filtered positive value.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.indicators.valuation import compute_pe_pb_percentile


def _hist(values: list[float]) -> pd.DataFrame:
    n = len(values)
    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="W")
    return pd.DataFrame({"date": dates, "pe_ttm": values})


class TestComputePePbPercentile:
    def test_normal_positive_series(self):
        df = _hist([10.0] * 60 + [5.0])  # 末值更低
        r = compute_pe_pb_percentile(df, "pe_ttm", years=5)
        assert r["current_valid"] is True
        assert r["current"] == 5.0
        assert r["percentile"] is not None
        assert 0 <= r["percentile"] <= 100
        assert r["sample_count"] >= 30

    def test_negative_current_with_positive_history(self):
        """Regression: 亏损股当前 PE 为负、历史全正 → current_valid=False, percentile=None."""
        df = _hist([20.0] * 80 + [-15.0])
        r = compute_pe_pb_percentile(df, "pe_ttm", years=5)
        assert r["current_valid"] is False
        assert r["current"] == -15.0
        assert r["percentile"] is None
        # 历史样本仍被统计
        assert r["sample_count"] >= 30
        assert r["median"] is not None

    def test_extreme_current_with_normal_history(self):
        """极端值（>=1000）也算 invalid."""
        df = _hist([20.0] * 80 + [5000.0])
        r = compute_pe_pb_percentile(df, "pe_ttm", years=5)
        assert r["current_valid"] is False
        assert r["current"] == 5000.0
        assert r["percentile"] is None

    def test_insufficient_samples_returns_empty(self):
        df = _hist([10.0] * 10)
        r = compute_pe_pb_percentile(df, "pe_ttm", years=5)
        assert r["percentile"] is None
        assert r["sample_count"] == 10
        # 末值 10.0 仍是合法正值；current_valid 反映末行原始值
        assert r["current_valid"] is True
        assert r["current"] == 10.0

    def test_empty_dataframe(self):
        df = pd.DataFrame(columns=["date", "pe_ttm"])
        r = compute_pe_pb_percentile(df, "pe_ttm", years=5)
        assert r["percentile"] is None
        assert r["sample_count"] == 0
