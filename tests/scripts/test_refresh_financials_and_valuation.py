"""refresh_financials_and_valuation 脚本单元测试。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.refresh_financials_and_valuation import (
    _refresh_one,
    _should_skip_code,
)
from src.storage import DuckDBStore


class ValuationSource:
    def __init__(self, pe: pd.DataFrame):
        self.pe = pe
        self.calls = 0

    def get_pe_pb_history(self, code: str, years: int = 5) -> pd.DataFrame:
        self.calls += 1
        return self.pe


class FinancialSource:
    def __init__(self, fin: pd.DataFrame):
        self.fin = fin
        self.calls = 0

    def get_financial_abstract(self, code: str) -> pd.DataFrame:
        self.calls += 1
        return self.fin


def _pe_history(n: int) -> pd.DataFrame:
    dates = pd.date_range(end=pd.Timestamp(2026, 7, 1), periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "close": [10.0] * n,
        "pe_ttm": [20.0] * n,
        "pb": [2.0] * n,
    })


def _financials() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "report_date": pd.Timestamp(2025, 12, 31),
            "revenue": 100.0,
            "deducted_net_profit": 10.0,
            "revenue_yoy": 0.2,
        }
    ])


def test_should_not_skip_single_snapshot_when_history_required(tmp_path: Path):
    store = DuckDBStore(db_path=tmp_path / "t.duckdb")
    try:
        store.save_pe_pb_history("600031", _pe_history(1))
        store.save_financials("600031", _financials())

        assert _should_skip_code(
            store, "600031", force=False, min_pe_history_rows=1
        )
        assert not _should_skip_code(
            store, "600031", force=False, min_pe_history_rows=100
        )
        assert not _should_skip_code(
            store, "600031", force=True, min_pe_history_rows=1
        )
    finally:
        store.close()


def test_refresh_one_uses_separate_valuation_and_financial_sources(tmp_path: Path):
    store = DuckDBStore(db_path=tmp_path / "t.duckdb")
    try:
        valuation_source = ValuationSource(_pe_history(120))
        financial_source = FinancialSource(_financials())

        result = _refresh_one(
            store,
            valuation_source=valuation_source,
            financial_source=financial_source,
            code="600031",
        )

        assert result["pe"] == "ok(120)"
        assert result["fin"] == "ok(1)"
        assert valuation_source.calls == 1
        assert financial_source.calls == 1
        assert len(store.load_pe_pb_history("600031")) == 120
        assert len(store.load_financials("600031")) == 1
    finally:
        store.close()
