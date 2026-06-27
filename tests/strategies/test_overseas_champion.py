"""策略三旧 CSV 入口 run_overseas_champion 的回归测试。

历史问题：run_overseas_champion 曾经有两个 _evaluate_one 定义，
Python 后定义覆盖前定义，导致旧入口走的是旧的内联实现，不走状态化路径。
P1-2/P1-3 增强只对 run_after_disclosure.py 生效，对 run_phase3_strategy3.py 失效。

本测试直接调 run_overseas_champion，pin 住：
- hit 案例返回 1 行 DataFrame
- parse_warning 触发 watch 不出现在 CSV（旧接口只输出 hit）
- ratio 异常 / PE 过高被 rejected 不出现在 CSV
- legacy CSV 列（overseas_ratio / pe_ttm_current / ocf_to_profit 等）存在
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.storage import DuckDBStore
from src.strategies.overseas_champion import (
    StrategyConfig,
    run_overseas_champion,
)


class MockSource:
    def __init__(self, pe_hist: pd.DataFrame, fin: pd.DataFrame):
        self._pe = pe_hist
        self._fin = fin

    def get_pe_pb_history(self, code: str, years: int = 5):
        return self._pe

    def get_financial_abstract(self, code: str):
        return self._fin


def _pe_history_low(n: int = 120, current: float = 20.0) -> pd.DataFrame:
    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="W")
    return pd.DataFrame({"date": dates, "pe_ttm": [50.0] * (n - 1) + [current]})


def _fin_with_revenue(revenue: float = 1e10, year: int = 2025) -> pd.DataFrame:
    return pd.DataFrame([
        {"report_date": pd.Timestamp(year=year, month=12, day=31),
         "revenue": revenue, "net_profit": revenue * 0.1,
         "deducted_net_profit": revenue * 0.1, "gross_margin": 0.3},
    ])


def _hit_config() -> StrategyConfig:
    """禁用需要 sina_source 的扩展条件，便于测试核心阈值。"""
    return StrategyConfig(
        require_cashflow_quality=False,
        require_leverage=False,
        require_consensus_growth=False,
        require_overseas_yoy=False,
    )


@pytest.fixture
def base_candidates():
    return pd.DataFrame(
        [{"code": "600031", "name": "三一重工", "sw_first": "机械设备"}]
    )


class TestRunOverseasChampionLegacyEntry:
    """直接调 run_overseas_champion，验证它真的走 evaluate_overseas_full。"""

    def test_hit_returns_one_row(
        self, tmp_path: Path, base_candidates
    ):
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        store.save_overseas_revenue([
            {"stock_code": "600031", "report_year": 2025, "region_name": "境外",
             "revenue": 30.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": "",
             "candidates_json": "[]", "parse_warning": None, "confidence": "high"},
        ])
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        try:
            df = run_overseas_champion(
                source=source, store=store, candidates=base_candidates,
                config=_hit_config(), show_progress=False,
            )
            assert len(df) == 1
            assert df.iloc[0]["code"] == "600031"
            assert df.iloc[0]["overseas_ratio"] == pytest.approx(0.30, abs=0.01)
            assert df.iloc[0]["pe_ttm_current"] == 20.0
            # 新增 legacy 字段（从 metrics 拿）
            assert "ocf_to_profit" in df.columns
            assert "debt_ratio" in df.columns
        finally:
            store.close()

    def test_parse_warning_filters_out_of_csv(
        self, tmp_path: Path, base_candidates
    ):
        """parse_warning 触发 watch → 旧 CSV 入口不输出（只输出 hit）。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        store.save_overseas_revenue([
            {"stock_code": "600031", "report_year": 2025, "region_name": "境外",
             "revenue": 30.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": "",
             "candidates_json": "[]",
             "parse_warning": "cross_year_unit_anomaly",
             "confidence": "medium"},
        ])
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        try:
            df = run_overseas_champion(
                source=source, store=store, candidates=base_candidates,
                config=_hit_config(), show_progress=False,
            )
            # 关键：watch 不应进入 CSV（如果走旧的内联实现，会进入 CSV）
            assert len(df) == 0
        finally:
            store.close()

    def test_yoy_anomaly_filters_out_of_csv(
        self, tmp_path: Path, base_candidates
    ):
        """同比异常（|yoy|>5）→ watch → 不出现在 CSV。

        这是 P0 bug 的关键回归点：旧的内联 _evaluate_one 用 mask 过滤，
        但状态化路径直接 watch，根本不入 results。
        """
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        store.save_overseas_revenue([
            {"stock_code": "600031", "report_year": 2024, "region_name": "境外",
             "revenue": 1.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": ""},
            {"stock_code": "600031", "report_year": 2025, "region_name": "境外",
             "revenue": 100.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": ""},
        ])
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=2e10))
        try:
            df = run_overseas_champion(
                source=source, store=store, candidates=base_candidates,
                config=_hit_config(), show_progress=False,
            )
            assert len(df) == 0
        finally:
            store.close()

    def test_pe_too_high_filters_out(
        self, tmp_path: Path, base_candidates
    ):
        """PE > 25 → rejected → 不出现在 CSV。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        store.save_overseas_revenue([
            {"stock_code": "600031", "report_year": 2025, "region_name": "境外",
             "revenue": 30.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": "",
             "candidates_json": "[]", "parse_warning": None, "confidence": "high"},
        ])
        source = MockSource(_pe_history_low(current=50.0), _fin_with_revenue(revenue=1e10))
        try:
            df = run_overseas_champion(
                source=source, store=store, candidates=base_candidates,
                config=_hit_config(), show_progress=False,
            )
            assert len(df) == 0
        finally:
            store.close()

    def test_industry_not_target_returns_empty(
        self, tmp_path: Path
    ):
        """非目标行业的候选不进入评估，返回空 DataFrame。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        store.save_overseas_revenue([
            {"stock_code": "600031", "report_year": 2025, "region_name": "境外",
             "revenue": 30.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": ""},
        ])
        candidates = pd.DataFrame(
            [{"code": "600031", "name": "X", "sw_first": "钢铁"}]
        )
        source = MockSource(_pe_history_low(), _fin_with_revenue())
        try:
            df = run_overseas_champion(
                source=source, store=store, candidates=candidates,
                config=_hit_config(), show_progress=False,
            )
            assert df.empty
        finally:
            store.close()
