"""策略二A筛选脚本测试。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.screening import Status
from src.storage import DuckDBStore
from src.strategies.pharma_strategy import VbpRecoveryConfig, evaluate_vbp_recovery_one


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "run_pharma_vbp_recovery",
        PROJECT_ROOT / "scripts" / "run_pharma_vbp_recovery.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run_pharma_vbp_recovery = _load_script()


def test_cli_yoy_threshold_accepts_percent_and_decimal():
    assert run_pharma_vbp_recovery._normalize_cli_yoy_threshold(10.0) == 0.10
    assert run_pharma_vbp_recovery._normalize_cli_yoy_threshold(0.10) == 0.10


def test_run_screen_uses_local_industry_financials_and_vbp_events(tmp_path: Path):
    store = DuckDBStore(db_path=tmp_path / "pharma.duckdb")
    try:
        store.save_stock_industry(pd.DataFrame([
            {
                "code": "600276",
                "name": "恒瑞医药",
                "sina_industry": "",
                "sw_first": "医药生物",
                "sw_second": "化学制剂",
                "em2016": "医药生物-化学制药-化学制剂",
            },
            {
                "code": "600519",
                "name": "贵州茅台",
                "sina_industry": "",
                "sw_first": "食品饮料",
                "sw_second": "白酒",
            },
        ]))
        store.save_financials("600276", pd.DataFrame([
            {
                "report_date": "2025-03-31",
                "revenue_yoy": -3.0,
                "net_profit_yoy": -8.0,
                "deducted_net_profit": 90.0,
                "gross_margin": 28.0,
                "ocf_per_share": 0.1,
            },
            {
                "report_date": "2026-03-31",
                "revenue_yoy": 12.0,
                "net_profit_yoy": 8.0,
                "deducted_net_profit": 120.0,
                "gross_margin": 30.0,
                "ocf_per_share": 0.2,
            },
        ]))
        store.save_pharma_vbp_events(pd.DataFrame([
            {
                "code": "600276",
                "name": "恒瑞医药",
                "product_name": "药品A",
                "vbp_batch": "第八批",
                "vbp_status": "won",
                "tender_date": "2025-01-01",
                "source": "manual",
                "source_url": "https://example.com/source",
                "evidence_text": "中选",
            }
        ]))

        candidates = run_pharma_vbp_recovery._load_candidates(store)
        assert candidates["code"].tolist() == ["600276"]

        results = run_pharma_vbp_recovery.run_screen(
            store=store,
            run_id="r1",
            period="2026Q1",
            candidates=candidates,
            config_obj=VbpRecoveryConfig(),
        )
        assert len(results) == 1
        assert results[0].status == Status.HIT
        assert results[0].hit_reason == "vbp_recovery_confirmed"
    finally:
        store.close()


def test_write_outputs_creates_csv_and_report(tmp_path: Path):
    result = evaluate_vbp_recovery_one(
        candidate={
            "code": "600276",
            "name": "恒瑞医药",
            "sw_first": "医药生物",
            "sw_second": "化学制剂",
        },
        financials=pd.DataFrame([
            {
                "report_date": "2025-03-31",
                "revenue_yoy": 1.0,
                "net_profit_yoy": 1.0,
                "deducted_net_profit": 90.0,
                "gross_margin": 29.0,
                "ocf_per_share": 0.1,
            },
            {
                "report_date": "2026-03-31",
                "revenue_yoy": 10.0,
                "net_profit_yoy": 12.0,
                "deducted_net_profit": 120.0,
                "gross_margin": 31.0,
                "ocf_per_share": 0.2,
            },
        ]),
        vbp_events=pd.DataFrame([
            {
                "code": "600276",
                "name": "恒瑞医药",
                "product_name": "药品A",
                "vbp_batch": "第八批",
                "vbp_status": "won",
                "tender_date": "2025-01-01",
            }
        ]),
        run_id="r1",
        period="2026Q1",
    )
    run_pharma_vbp_recovery._write_outputs(
        "r1", "2026Q1", [result], tmp_path / "out",
    )
    assert (tmp_path / "out" / "pharma_vbp_2026Q1.csv").exists()
    assert "vbp_recovery_confirmed" in (tmp_path / "out" / "report.md").read_text()
