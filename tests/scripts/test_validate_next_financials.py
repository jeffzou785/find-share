"""validate_next_financials 脚本 helper 测试。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.storage import DuckDBStore


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "validate_next_financials",
        PROJECT_ROOT / "scripts" / "validate_next_financials.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_run_and_run_validation(tmp_path: Path):
    mod = _load_script()
    store = DuckDBStore(db_path=tmp_path / "t.duckdb")
    try:
        store.create_screen_run(
            "r1", "overseas", "2025A", "annual", "{}", "fp",
            input_count=1,
        )
        store.finish_screen_run("r1", "success", counts={"hit": 1})
        store.save_candidate_scores([
            {"run_id": "r1", "code": "600031", "name": "三一重工",
             "strategy": "overseas", "period": "2025A", "status": "hit",
             "hit_reason": "all_met"},
        ])
        store.save_financials("600031", pd.DataFrame([
            {
                "report_date": "2026-03-31",
                "revenue_yoy": 12.0,
                "net_profit_yoy": 8.0,
                "deducted_net_profit": 1_000_000,
                "gross_margin": 30.0,
                "ocf_per_share": 0.5,
            }
        ]))

        assert mod._resolve_run_id(
            store, run_id=None, strategy="overseas", period="2025A",
        ) == "r1"
        assert mod._resolve_run_id(
            store, run_id=None, strategy="all", period="2025A",
        ) == "r1"
        df = mod.run_validation(
            store=store,
            run_id="r1",
            validation_period=None,
            statuses=["hit"],
            min_revenue_yoy=0.05,
            min_net_profit_yoy=0.0,
        )
        assert len(df) == 1
        assert df.iloc[0]["verdict"] == "confirmed"
        assert df.iloc[0]["validation_period"] == "2026Q1"
    finally:
        store.close()
