"""P2 下一期财务验证测试。"""
from __future__ import annotations

import pandas as pd

from src.screening.financial_validation import (
    summarize_financial_validation,
    validate_next_financials,
    validate_next_financials_batch,
)


def _candidate(status: str = "hit"):
    return {
        "run_id": "r1",
        "code": "600031",
        "name": "三一重工",
        "strategy": "overseas",
        "period": "2025A",
        "status": status,
    }


def _financials(revenue_yoy=12.0, net_profit_yoy=8.0):
    return pd.DataFrame([
        {
            "report_date": "2026-03-31",
            "revenue_yoy": revenue_yoy,
            "net_profit_yoy": net_profit_yoy,
            "deducted_net_profit": 1_000_000_000,
            "gross_margin": 30.0,
            "ocf_per_share": 0.5,
        }
    ])


def test_validate_next_financials_confirmed_and_normalizes_percent_units():
    row = validate_next_financials(
        candidate=_candidate(),
        financials=_financials(revenue_yoy=12.0, net_profit_yoy=8.0),
        min_revenue_yoy=0.05,
        min_net_profit_yoy=0.0,
    )
    assert row["validation_period"] == "2026Q1"
    assert str(row["validation_report_date"].date()) == "2026-03-31"
    assert row["verdict"] == "confirmed"
    assert row["revenue_yoy"] == 0.12
    assert row["net_profit_yoy"] == 0.08
    assert row["gross_margin"] == 0.30


def test_validate_next_financials_mixed_when_one_check_fails():
    row = validate_next_financials(
        candidate=_candidate(),
        financials=_financials(revenue_yoy=10.0, net_profit_yoy=-5.0),
    )
    assert row["verdict"] == "mixed"


def test_validate_next_financials_pending_when_next_report_missing():
    row = validate_next_financials(
        candidate=_candidate(),
        financials=pd.DataFrame(columns=["report_date", "revenue_yoy"]),
    )
    assert row["verdict"] == "pending"
    assert row["error"] == "validation_financial_missing"


def test_validate_next_financials_batch_filters_statuses_and_summarizes():
    candidates = pd.DataFrame([
        _candidate("hit"),
        {**_candidate("rejected"), "code": "600519"},
    ])
    df = validate_next_financials_batch(
        candidates=candidates,
        financials_loader=lambda code: _financials(),
        statuses=["hit", "watch"],
    )
    assert len(df) == 1
    assert df.iloc[0]["code"] == "600031"
    summary = summarize_financial_validation(df)
    assert summary["total_rows"] == 1
    assert summary["verdicts"]["confirmed"] == 1
