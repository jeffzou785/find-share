"""策略三全量状态化测试（P0-5 / P0-6 / P0-12）。

测试 evaluate_overseas_full 的每条状态路径：
hit / rejected（多原因码）/ data_missing / watch (parse_warning)。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.screening import Status
from src.storage import DuckDBStore
from src.strategies.overseas_champion import (
    StrategyConfig,
    evaluate_overseas_full,
    is_yoy_anomaly,
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
    """构造 PE 历史：前 n-1 个样本高，最后 1 个 = current（低位）。"""
    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="W")
    return pd.DataFrame({"date": dates, "pe_ttm": [50.0] * (n - 1) + [current]})


def _fin_with_revenue(revenue: float = 1e10, year: int = 2025) -> pd.DataFrame:
    return pd.DataFrame([
        {"report_date": pd.Timestamp(year=year, month=12, day=31),
         "revenue": revenue, "net_profit": revenue * 0.1,
         "deducted_net_profit": revenue * 0.1, "gross_margin": 0.3},
    ])


def _candidates_json(records: list[dict]) -> str:
    """构造 candidates_json 字符串。

    每条记录至少含 region_name / revenue / revenue_unit / revenue_yuan /
    is_total_row / confidence 字段（与 parser._record_to_candidate_dict 对齐）。
    """
    import json
    return json.dumps(records, ensure_ascii=False)


@pytest.fixture
def store_with_overseas(tmp_path: Path):
    """构造一个 DuckDBStore 并预填 overseas_revenue。

    默认填入 600031 的 2025 年海外收入 30 亿（30*1e8）。
    """
    store = DuckDBStore(db_path=tmp_path / "t.duckdb")
    rows = [
        {
            "stock_code": "600031", "report_year": 2025,
            "region_name": "境外", "revenue": 30.0, "revenue_unit": "亿元",
            "source_page": 1, "raw_text": "", "pdf_path": "",
        },
    ]
    store.save_overseas_revenue(rows)
    yield store
    store.close()


@pytest.fixture
def base_candidates():
    return pd.DataFrame(
        [{"code": "600031", "name": "三一重工", "sw_first": "机械设备"}]
    )


@pytest.fixture
def hit_config():
    """禁用 cashflow/leverage（它们需要 sina_source 才能算），便于测试核心阈值。"""
    return StrategyConfig(
        require_cashflow_quality=False,
        require_leverage=False,
        require_consensus_growth=False,
        require_overseas_yoy=False,
    )


class TestEvaluateOverseasFull:
    def test_overseas_revenue_missing_data_missing(
        self, store_with_overseas, base_candidates, hit_config
    ):
        # 候选股没在 overseas_revenue 表里
        candidates = pd.DataFrame(
            [{"code": "000001", "name": "X", "sw_first": "机械设备"}]
        )
        source = MockSource(_pe_history_low(), _fin_with_revenue())
        results = evaluate_overseas_full(
            source=source, store=store_with_overseas,
            candidates=candidates, run_id="r1", period="2025A",
            show_progress=False, config=hit_config, sina_source=None,
        )
        assert results[0].status == Status.DATA_MISSING
        assert results[0].data_missing_reason == "overseas_revenue_missing"

    def test_financial_data_missing(
        self, store_with_overseas, base_candidates, hit_config
    ):
        source = MockSource(_pe_history_low(), pd.DataFrame())
        results = evaluate_overseas_full(
            source=source, store=store_with_overseas,
            candidates=base_candidates, run_id="r1", period="2025A",
            show_progress=False, config=hit_config, sina_source=None,
        )
        assert results[0].status == Status.DATA_MISSING
        assert results[0].data_missing_reason == "financial_data_missing"

    def test_overseas_ratio_too_low_rejected(
        self, store_with_overseas, base_candidates, hit_config
    ):
        # 海外 30 亿，营收 200 亿 → ratio = 0.15 < 0.30
        source = MockSource(_pe_history_low(), _fin_with_revenue(revenue=2e10))
        results = evaluate_overseas_full(
            source=source, store=store_with_overseas,
            candidates=base_candidates, run_id="r1", period="2025A",
            show_progress=False, config=hit_config, sina_source=None,
        )
        assert results[0].status == Status.REJECTED
        assert results[0].reject_reason == "overseas_ratio_too_low"

    def test_overseas_ratio_abnormal_rejected(
        self, store_with_overseas, base_candidates, hit_config
    ):
        # 海外 30 亿，营收 30.5 亿 → ratio ≈ 0.98 ≥ 0.95
        source = MockSource(_pe_history_low(), _fin_with_revenue(revenue=3.05e9))
        results = evaluate_overseas_full(
            source=source, store=store_with_overseas,
            candidates=base_candidates, run_id="r1", period="2025A",
            show_progress=False, config=hit_config, sina_source=None,
        )
        assert results[0].status == Status.REJECTED
        assert results[0].reject_reason == "overseas_ratio_abnormal"

    def test_pe_ttm_too_high_rejected(
        self, store_with_overseas, base_candidates, hit_config
    ):
        # 海外 30 亿，营收 100 亿 → ratio = 0.30 通过；PE = 50 > 25
        source = MockSource(_pe_history_low(current=50.0), _fin_with_revenue())
        results = evaluate_overseas_full(
            source=source, store=store_with_overseas,
            candidates=base_candidates, run_id="r1", period="2025A",
            show_progress=False,
            config=StrategyConfig(
                require_cashflow_quality=False, require_leverage=False,
                require_consensus_growth=False, require_overseas_yoy=False,
            ),
            sina_source=None,
        )
        assert results[0].status == Status.REJECTED
        assert results[0].reject_reason == "pe_ttm_too_high"

    def test_yoy_anomaly_goes_to_watch(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        """同比异常（|yoy|>5 或 <-0.8）应进入 watch，而非 rejected。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        # 2024: 1 亿；2025: 100 亿 → yoy ≈ 99（异常）
        rows = [
            {"stock_code": "600031", "report_year": 2024, "region_name": "境外",
             "revenue": 1.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": ""},
            {"stock_code": "600031", "report_year": 2025, "region_name": "境外",
             "revenue": 100.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": ""},
        ]
        store.save_overseas_revenue(rows)
        source = MockSource(_pe_history_low(), _fin_with_revenue(revenue=2e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store,
                candidates=base_candidates, run_id="r1", period="2025A",
                show_progress=False, config=hit_config, sina_source=None,
            )
            r = results[0]
            assert r.status == Status.WATCH
            assert r.watch_reason == "parse_warning"
            assert r.metrics.overseas.parse_warning is not None
        finally:
            store.close()

    def test_all_thresholds_met_hit(
        self, store_with_overseas, base_candidates, hit_config
    ):
        # 海外 30 亿，营收 100 亿 → ratio = 0.30 ✓
        # PE = 20 ≤ 25 ✓
        # 单年数据，不要求 yoy ✓
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        results = evaluate_overseas_full(
            source=source, store=store_with_overseas,
            candidates=base_candidates, run_id="r1", period="2025A",
            show_progress=False, config=hit_config, sina_source=None,
        )
        r = results[0]
        assert r.status == Status.HIT
        assert r.hit_reason == "all_thresholds_met"
        assert r.metrics.overseas.overseas_ratio == pytest.approx(0.30, abs=0.01)
        assert r.metrics.valuation.pe_ttm == 20.0
        assert r.metrics.source_status.extra["overseas_year_count"] == "1"
        assert r.metrics.source_status.extra["overseas_yoy_status"] == "single_year"

    def test_low_overseas_yoy_rejected_when_two_years_available(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        """连续两年海外收入可用时，即使 require_overseas_yoy=False 也校验同比。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        # 2024: 29 亿；2025: 30 亿 → yoy≈3.4%，低于 40%。
        rows = [
            {"stock_code": "600031", "report_year": 2024, "region_name": "境外",
             "revenue": 29.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": ""},
            {"stock_code": "600031", "report_year": 2025, "region_name": "境外",
             "revenue": 30.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": ""},
        ]
        store.save_overseas_revenue(rows)
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store,
                candidates=base_candidates, run_id="r1", period="2025A",
                show_progress=False, config=hit_config, sina_source=None,
            )
            r = results[0]
            assert r.status == Status.REJECTED
            assert r.reject_reason == "overseas_yoy_abnormal"
            assert r.metrics.overseas.overseas_yoy == pytest.approx(1.0 / 29.0, abs=1e-6)
            assert r.metrics.source_status.extra["overseas_yoy_status"] == "ok"
        finally:
            store.close()

    def test_missing_previous_year_yoy_goes_to_data_warning(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        """有两年海外收入但不连续时，无法做同比，应降级 watch 而非误判 hit。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        rows = [
            {"stock_code": "600031", "report_year": 2023, "region_name": "境外",
             "revenue": 20.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": ""},
            {"stock_code": "600031", "report_year": 2025, "region_name": "境外",
             "revenue": 30.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": ""},
        ]
        store.save_overseas_revenue(rows)
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store,
                candidates=base_candidates, run_id="r1", period="2025A",
                show_progress=False, config=hit_config, sina_source=None,
            )
            r = results[0]
            assert r.status == Status.WATCH
            assert r.watch_reason == "data_warning"
            assert r.metrics.source_status.extra["overseas_year_count"] == "2"
            assert r.metrics.source_status.extra["overseas_yoy_status"] == "missing_prev_year"
        finally:
            store.close()

    def test_latest_invalid_overseas_revenue_does_not_fallback_to_old_year(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        """最新年海外收入坏值时，不能用上一年正值冒充最新年数据。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        rows = [
            {"stock_code": "600031", "report_year": 2024, "region_name": "境外",
             "revenue": 30.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": ""},
            {"stock_code": "600031", "report_year": 2025, "region_name": "境外",
             "revenue": 0.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": ""},
        ]
        store.save_overseas_revenue(rows)
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store,
                candidates=base_candidates, run_id="r1", period="2025A",
                show_progress=False, config=hit_config, sina_source=None,
            )
            r = results[0]
            assert r.status == Status.DATA_MISSING
            assert r.data_missing_reason == "overseas_revenue_missing"
            assert r.metrics.source_status.extra["overseas_year_count"] == "1"
            assert r.metrics.source_status.extra["overseas_yoy_status"] == "missing"
        finally:
            store.close()

    def test_industry_filter_excluded(self, store_with_overseas, hit_config):
        source = MockSource(_pe_history_low(), _fin_with_revenue())
        candidates = pd.DataFrame(
            [{"code": "600031", "name": "X", "sw_first": "钢铁"}]
        )
        results = evaluate_overseas_full(
            source=source, store=store_with_overseas,
            candidates=candidates, run_id="r1", period="2025A",
            show_progress=False, config=hit_config, sina_source=None,
        )
        assert results == []

    def test_metrics_filled_on_hit(
        self, store_with_overseas, base_candidates, hit_config
    ):
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        results = evaluate_overseas_full(
            source=source, store=store_with_overseas,
            candidates=base_candidates, run_id="r1", period="2025A",
            show_progress=False, config=hit_config, sina_source=None,
        )
        m = results[0].metrics
        assert m.overseas.overseas_revenue_yi == pytest.approx(30.0, abs=0.01)
        assert m.valuation.pe_ttm == 20.0


class TestP13ParseWarningFromDb:
    """P1-3：overseas_revenue.parse_warning / confidence 进入 watch。"""

    def test_db_parse_warning_triggers_watch(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        """DB 里 parse_warning 非空 → 评估进入 watch（不是 hit）。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        rows = [
            {"stock_code": "600031", "report_year": 2025, "region_name": "境外",
             "revenue": 30.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": "",
             "candidates_json": "[]",
             "parse_warning": "cross_year_unit_anomaly:2025=30.00yi vs 2024=0.30yi ratio=100x",
             "confidence": "medium"},
        ]
        store.save_overseas_revenue(rows)
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store,
                candidates=base_candidates, run_id="r1", period="2025A",
                show_progress=False, config=hit_config, sina_source=None,
            )
            r = results[0]
            assert r.status == Status.WATCH
            assert r.watch_reason == "parse_warning"
            assert r.metrics.overseas.parse_warning is not None
            assert "cross_year_unit_anomaly" in r.metrics.overseas.parse_warning
        finally:
            store.close()

    def test_db_low_confidence_triggers_data_warning_watch(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        """confidence=low → 进入 watch（watch_reason=data_warning）。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        rows = [
            {"stock_code": "600031", "report_year": 2025, "region_name": "境外",
             "revenue": 30.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": "",
             "candidates_json": "[]",
             "parse_warning": None,
             "confidence": "low"},
        ]
        store.save_overseas_revenue(rows)
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store,
                candidates=base_candidates, run_id="r1", period="2025A",
                show_progress=False, config=hit_config, sina_source=None,
            )
            r = results[0]
            assert r.status == Status.WATCH
            assert r.watch_reason == "data_warning"
        finally:
            store.close()

    def test_db_high_confidence_no_warning_hits(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        """confidence=high 且无 parse_warning → 正常 hit（不被 watch 拦截）。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        rows = [
            {"stock_code": "600031", "report_year": 2025, "region_name": "境外",
             "revenue": 30.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": "",
             "candidates_json": "[]",
             "parse_warning": None,
             "confidence": "high"},
        ]
        store.save_overseas_revenue(rows)
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store,
                candidates=base_candidates, run_id="r1", period="2025A",
                show_progress=False, config=hit_config, sina_source=None,
            )
            r = results[0]
            assert r.status == Status.HIT
        finally:
            store.close()


class TestP12ReportsCoverage:
    """P1-2：研报覆盖度（被忽视证据）填入 metrics.catalyst。"""

    def test_reports_count_zero_when_no_data(
        self, store_with_overseas, base_candidates, hit_config
    ):
        """broker_reports 表无数据 → reports_count_90d=0（不报错）。"""
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        results = evaluate_overseas_full(
            source=source, store=store_with_overseas,
            candidates=base_candidates, run_id="r1", period="2025A",
            show_progress=False, config=hit_config, sina_source=None,
        )
        assert results[0].metrics.catalyst.reports_count_90d == 0

    def test_reports_count_filled_from_broker_reports(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        """预填 5 份近 90 天研报 → reports_count_90d=5。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        store.save_overseas_revenue([
            {"stock_code": "600031", "report_year": 2025, "region_name": "境外",
             "revenue": 30.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": "",
             "candidates_json": "[]", "parse_warning": None, "confidence": "high"},
        ])
        # 预填 5 份研报（近 30 天内）
        now = pd.Timestamp.now()
        broker_rows = []
        for i in range(5):
            broker_rows.append({
                "code": "600031", "stock_name": "三一重工",
                "title": f"研报 {i}", "broker": f"券商{i}",
                "broker_code": f"B{i}", "rating": "买入",
                "rating_prev": "买入", "rating_idx": 1.0,
                "eps_forecast_y1": 1.0, "eps_forecast_y2": 1.2,
                "eps_forecast_y3": 1.5, "pe_forecast_y1": 20.0,
                "pe_forecast_y2": 18.0, "pe_forecast_y3": 15.0,
                "industry": "机械设备",
                "publish_date": (now - pd.Timedelta(days=i*5)).date(),
                "research_date": (now - pd.Timedelta(days=i*5)).date(),
                "info_code": f"IC{i}", "report_id": f"R{i}",
                "pdf_path": None, "ingested_to_rag": False,
            })
        store.save_broker_reports(pd.DataFrame(broker_rows))
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store,
                candidates=base_candidates, run_id="r1", period="2025A",
                show_progress=False, config=hit_config, sina_source=None,
            )
            r = results[0]
            assert r.metrics.catalyst.reports_count_90d == 5
        finally:
            store.close()


class TestP152RatioCheckFromCandidatesJson:
    """P1.5-2：策略层从 candidates_json 选合理候选。

    模拟 600690 场景：parser best=1429yi（误抓总营收）→ ratio>0.95，
    策略层尝试 candidates_json 中的 62yi（ratio=0.4），落入 watch。
    """

    def test_implausible_ratio_fallbacks_to_candidate(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        # best=200yi（明显超过总营收 100yi），ratio=2.0 不合理
        cands = _candidates_json([
            {"region_name": "境外", "revenue": 200.0, "revenue_unit": "亿元",
             "revenue_yuan": 200.0e8, "is_total_row": False, "confidence": "high"},
            {"region_name": "境外", "revenue": 30.0, "revenue_unit": "亿元",
             "revenue_yuan": 30.0e8, "is_total_row": False, "confidence": "high"},
        ])
        store.save_overseas_revenue([{
            "stock_code": "600031", "report_year": 2025, "region_name": "境外",
            "revenue": 200.0, "revenue_unit": "亿元",
            "source_page": 1, "raw_text": "", "pdf_path": "",
            "candidates_json": cands, "parse_warning": None, "confidence": "high",
        }])
        # 总营收 100 亿
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store, candidates=base_candidates,
                run_id="r1", period="2025A", show_progress=False,
                config=hit_config, sina_source=None,
            )
            r = results[0]
            # 选中 30yi（ratio=0.3）而非 200yi（ratio=2.0）
            assert r.status == Status.WATCH
            assert r.watch_reason == "parse_warning"
            assert r.metrics.overseas.overseas_revenue_yi == pytest.approx(30.0, abs=0.1)
            assert r.metrics.overseas.overseas_ratio == pytest.approx(0.3, abs=0.01)
            assert "candidate_chose_from_json" in (r.metrics.overseas.parse_warning or "")
        finally:
            store.close()

    def test_plausible_best_does_not_trigger_fallback(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        """best=30yi / total=100yi → ratio=0.3 合理，不触发 candidates fallback。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        cands = _candidates_json([
            {"region_name": "境外", "revenue": 30.0, "revenue_unit": "亿元",
             "revenue_yuan": 30.0e8, "is_total_row": False, "confidence": "high"},
        ])
        store.save_overseas_revenue([{
            "stock_code": "600031", "report_year": 2025, "region_name": "境外",
            "revenue": 30.0, "revenue_unit": "亿元",
            "source_page": 1, "raw_text": "", "pdf_path": "",
            "candidates_json": cands, "parse_warning": None, "confidence": "high",
        }])
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store, candidates=base_candidates,
                run_id="r1", period="2025A", show_progress=False,
                config=hit_config, sina_source=None,
            )
            r = results[0]
            assert r.status == Status.HIT
            assert r.metrics.overseas.overseas_ratio == pytest.approx(0.3, abs=0.01)
            assert r.metrics.overseas.parse_warning is None
        finally:
            store.close()


class TestIsYoyAnomaly:
    def test_normal_yoy_not_anomaly(self):
        assert is_yoy_anomaly(0.5) is False
        assert is_yoy_anomaly(-0.3) is False

    def test_extreme_high_anomaly(self):
        assert is_yoy_anomaly(6.0) is True

    def test_extreme_drop_anomaly(self):
        assert is_yoy_anomaly(-0.9) is True

    def test_none_not_anomaly(self):
        assert is_yoy_anomaly(None) is False


class TestP22QuarterlyPeriod:
    """P2-2 半年报/季报扩展：季报场景不强求海外收入硬过滤。"""

    def test_q1_without_overseas_data_goes_through(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        """季报场景下候选股没有 overseas_revenue 不应被判 data_missing。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")  # 不预填 overseas
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store,
                candidates=base_candidates, run_id="r1", period="2025Q1",
                show_progress=False, config=hit_config, sina_source=None,
            )
            r = results[0]
            # 不应进入 data_missing；应通过 PE/财务阈值（海外跳过）
            assert r.status != Status.DATA_MISSING
            assert r.metrics.source_status.overseas_parser == "missing"
        finally:
            store.close()

    def test_q1_skips_overseas_ratio_reject(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        """季报场景下即使 overseas_ratio < min 也不应被 rejected。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        # 海外 30 亿，营收 200 亿 → ratio = 0.15 < 0.30
        # 年报会被判 overseas_ratio_too_low；季报应跳过
        rows = [
            {"stock_code": "600031", "report_year": 2024, "region_name": "境外",
             "revenue": 30.0, "revenue_unit": "亿元",
             "source_page": 1, "raw_text": "", "pdf_path": ""},
        ]
        store.save_overseas_revenue(rows)
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=2e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store,
                candidates=base_candidates, run_id="r1", period="2025Q1",
                show_progress=False, config=hit_config, sina_source=None,
            )
            r = results[0]
            assert r.status == Status.HIT
            # 季报场景 hit_reason 仍是 all_thresholds_met；source_status 标 overseas missing
            # 但 overseas_ratio 仍填入 metrics 作为 context（不参与硬过滤）
            assert r.metrics.overseas.overseas_ratio is not None
        finally:
            store.close()

    def test_q1_pe_still_filtered(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        """季报场景下 PE 阈值仍然生效。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        source = MockSource(_pe_history_low(current=50.0), _fin_with_revenue(revenue=1e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store,
                candidates=base_candidates, run_id="r1", period="2025Q1",
                show_progress=False, config=hit_config, sina_source=None,
            )
            r = results[0]
            assert r.status == Status.REJECTED
            assert r.reject_reason == "pe_ttm_too_high"
        finally:
            store.close()

    def test_annual_still_requires_overseas(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        """年报场景下没有 overseas_revenue 仍判 data_missing。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store,
                candidates=base_candidates, run_id="r1", period="2025A",
                show_progress=False, config=hit_config, sina_source=None,
            )
            r = results[0]
            assert r.status == Status.DATA_MISSING
            assert r.data_missing_reason == "overseas_revenue_missing"
        finally:
            store.close()

    def test_half_year_still_requires_overseas(
        self, tmp_path: Path, base_candidates, hit_config
    ):
        """半年报场景仍要求 overseas_revenue（附注较全）。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        source = MockSource(_pe_history_low(current=20.0), _fin_with_revenue(revenue=1e10))
        try:
            results = evaluate_overseas_full(
                source=source, store=store,
                candidates=base_candidates, run_id="r1", period="2025H",
                show_progress=False, config=hit_config, sina_source=None,
            )
            r = results[0]
            assert r.status == Status.DATA_MISSING
            assert r.data_missing_reason == "overseas_revenue_missing"
        finally:
            store.close()
