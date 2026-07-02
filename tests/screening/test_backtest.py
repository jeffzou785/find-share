"""P2 forward-return backtest tests."""
from __future__ import annotations

import pandas as pd
import pytest

from src.screening.backtest import (
    compute_forward_returns,
    compute_forward_returns_batch,
    normalize_windows,
    summarize_backtest,
)


def _prices(start: str = "2026-01-01", n: int = 130, base: float = 100.0, step: float = 1.0):
    return pd.DataFrame({
        "date": pd.date_range(start, periods=n, freq="D"),
        "close": [base + i * step for i in range(n)],
    })


def test_compute_forward_returns_uses_trading_row_windows_and_relative_return():
    candidate = {
        "run_id": "r1",
        "code": "600276",
        "name": "恒瑞医药",
        "strategy": "pharma",
        "period": "2025A",
    }
    rows = compute_forward_returns(
        candidate=candidate,
        anchor_date="2026-01-01",
        price_history=_prices(base=100, step=1),
        benchmark_history=_prices(base=100, step=0.5),
        benchmark_code="000300",
        windows=(20,),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "ok"
    assert row["start_close"] == 100
    assert row["end_close"] == 120
    assert row["absolute_return"] == pytest.approx(0.20)
    assert row["benchmark_return"] == pytest.approx(0.10)
    assert row["relative_return"] == pytest.approx(0.10)


def test_compute_forward_returns_marks_insufficient_future_price():
    rows = compute_forward_returns(
        candidate={"run_id": "r1", "code": "1", "strategy": "consumer"},
        anchor_date="2026-01-01",
        price_history=_prices(n=5),
        windows=(20,),
    )
    assert rows[0]["status"] == "missing"
    assert rows[0]["error"] == "insufficient_future_price"
    assert rows[0]["code"] == "000001"


def test_compute_forward_returns_marks_start_lag():
    rows = compute_forward_returns(
        candidate={"run_id": "r1", "code": "600276", "strategy": "pharma"},
        anchor_date="2026-01-01",
        price_history=_prices(start="2026-02-01", n=30),
        windows=(20,),
        max_start_lag_days=10,
    )
    assert rows[0]["status"] == "missing"
    assert rows[0]["error"] == "start_price_lag_gt_10d"


def test_compute_forward_returns_marks_missing_benchmark_when_requested():
    rows = compute_forward_returns(
        candidate={"run_id": "r1", "code": "600276", "strategy": "pharma"},
        anchor_date="2026-01-01",
        price_history=_prices(n=30),
        windows=(20,),
        benchmark_code="000300",
    )
    assert rows[0]["status"] == "partial"
    assert rows[0]["error"] == "benchmark_price_history_missing"
    assert rows[0]["relative_return"] is None


def test_normalize_windows_rejects_non_positive_values():
    assert normalize_windows([20, 20, 60]) == (20, 60)
    with pytest.raises(ValueError, match="positive"):
        normalize_windows([20, 0])


def test_compute_forward_returns_batch_filters_statuses_and_summarizes():
    candidates = pd.DataFrame([
        {"run_id": "r1", "code": "600276", "strategy": "pharma", "status": "hit"},
        {"run_id": "r1", "code": "600519", "strategy": "consumer", "status": "rejected"},
    ])
    df = compute_forward_returns_batch(
        candidates=candidates,
        anchor_date="2026-01-01",
        price_loader=lambda code: _prices(base=100),
        windows=(20,),
    )
    assert len(df) == 1
    assert df.iloc[0]["code"] == "600276"
    summary = summarize_backtest(df)
    assert summary["total_rows"] == 1
    assert summary["windows"][20]["ok_rows"] == 1


def test_compute_forward_returns_batch_allows_pre_filtered_candidates_without_status():
    candidates = pd.DataFrame([
        {"run_id": "r1", "code": "600276", "strategy": "pharma"},
    ])
    df = compute_forward_returns_batch(
        candidates=candidates,
        anchor_date="2026-01-01",
        price_loader=lambda code: _prices(base=100),
        windows=(20,),
    )
    assert len(df) == 1
    assert df.iloc[0]["status"] == "ok"
