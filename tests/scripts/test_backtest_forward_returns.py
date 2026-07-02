"""backtest_forward_returns 脚本 helper 测试。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.storage import DuckDBStore


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "backtest_forward_returns",
        PROJECT_ROOT / "scripts" / "backtest_forward_returns.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _prices(n: int = 130, base: float = 100.0, step: float = 1.0):
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n, freq="D"),
        "close": [base + i * step for i in range(n)],
    })


def test_resolve_run_and_run_backtest(tmp_path: Path):
    mod = _load_script()
    store = DuckDBStore(db_path=tmp_path / "t.duckdb")
    try:
        store.create_screen_run(
            "r1", "overseas", "2025A", "annual", "{}", "fp",
            input_count=1,
        )
        store.finish_screen_run("r1", "success", counts={"hit": 1})
        store.save_candidate_scores([
            {"run_id": "r1", "code": "600276", "name": "恒瑞医药",
             "strategy": "overseas", "period": "2025A", "status": "hit",
             "hit_reason": "all_met"},
        ])
        store.save_pe_pb_history("600276", _prices(base=100, step=1))
        store.save_pe_pb_history("000300", _prices(base=100, step=0.5))

        assert mod._resolve_run_id(
            store, run_id=None, strategy="overseas", period="2025A",
        ) == "r1"
        assert mod._resolve_run_id(
            store, run_id=None, strategy="all", period="2025A",
        ) == "r1"
        df = mod.run_backtest(
            store=store,
            run_id="r1",
            anchor_date=pd.Timestamp("2026-01-01"),
            windows=[20],
            statuses=["hit"],
            benchmark_code="000300",
            max_start_lag_days=10,
        )
        assert len(df) == 1
        assert df.iloc[0]["absolute_return"] == pytest.approx(0.2)
        assert df.iloc[0]["relative_return"] == pytest.approx(0.1)
    finally:
        store.close()
