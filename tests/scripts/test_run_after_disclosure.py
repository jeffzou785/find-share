"""run_after_disclosure.py 关键 helper 测试（P0-7 / P0-9）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_after_disclosure import (
    _build_config_schema,
    _build_coverage_report,
    _filter_candidates_by_codes,
    _gen_run_id,
    _load_skip_codes_for_resume,
    _merge_fingerprints,
    _period_to_report_type,
    _resolve_codes,
    _serialize_run_config,
)
from src.screening import MetricsSchema, ScreeningResult
from src.storage import DuckDBStore


class TestGenRunId:
    def test_format_contains_period(self):
        run_id = _gen_run_id("2025A")
        assert "2025A" in run_id
        # YYYYMMDD_HHMMSS_xxxx_2025A
        parts = run_id.split("_")
        assert len(parts) == 4
        assert len(parts[2]) == 4  # 4 位 hex


class TestPeriodToReportType:
    @pytest.mark.parametrize(
        "period,expected",
        [
            ("2025A", "annual"),
            ("2025H", "half_year"),
            ("2025Q1", "q1"),
            ("2025Q3", "q3"),
            ("", "annual"),
            ("2025", "annual"),
        ],
    )
    def test_known_periods(self, period, expected):
        assert _period_to_report_type(period) == expected


class TestFilterCandidatesByCodes:
    def test_filter_returns_matching(self):
        candidates = pd.DataFrame(
            [{"code": "600031", "name": "A", "sw_first": "机械"},
             {"code": "000001", "name": "B", "sw_first": "银行"}]
        )
        result = _filter_candidates_by_codes(candidates, ["600031"])
        assert len(result) == 1
        assert result.iloc[0]["code"] == "600031"

    def test_filter_handles_unpadded_codes(self):
        """输入短代码 '31' → zero-pad 后是 '000031'，应匹配候选 '000031'。"""
        candidates = pd.DataFrame([{"code": "000031", "name": "A"}])
        result = _filter_candidates_by_codes(candidates, ["31"])
        assert len(result) == 1

    def test_filter_empty_codes_returns_empty(self):
        candidates = pd.DataFrame([{"code": "600031"}])
        assert _filter_candidates_by_codes(candidates, []).empty


class TestResolveCodes:
    def test_explicit_codes_win(self, tmp_path):
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        try:
            candidates = pd.DataFrame(
                [{"code": "600031", "name": "A", "sw_first": "X"}]
            )
            class Args:
                codes = ["000001"]
                from_disclosures = False
                limit = None
            codes = _resolve_codes(args=Args, store=store, period="2025A",
                                   candidates=candidates)
            assert codes == ["000001"]
        finally:
            store.close()

    def test_fallback_to_candidates_when_no_disclosures(self, tmp_path):
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        try:
            candidates = pd.DataFrame(
                [{"code": "600031", "name": "A"},
                 {"code": "000001", "name": "B"}]
            )
            class Args:
                codes = None
                from_disclosures = True
                limit = 1
            codes = _resolve_codes(args=Args, store=store, period="2025A",
                                   candidates=candidates)
            # disclosures 为空，回退到候选池，limit=1
            assert len(codes) == 1
        finally:
            store.close()

    def test_uses_disclosures_when_available(self, tmp_path):
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        try:
            disc_df = pd.DataFrame([
                {"code": "600031", "name": "A", "period": "2025A",
                 "first_schedule": None,
                 "actual_date": pd.Timestamp("2026-03-15")},
                {"code": "000001", "name": "B", "period": "2025A",
                 "first_schedule": None, "actual_date": None},
            ])
            store.save_disclosures(disc_df, "2025A")
            candidates = pd.DataFrame(
                [{"code": "999999", "name": "X"}]
            )
            class Args:
                codes = None
                from_disclosures = True
                limit = None
            codes = _resolve_codes(args=Args, store=store, period="2025A",
                                   candidates=candidates)
            # 只取 actual_date 非空的
            assert codes == ["600031"]
        finally:
            store.close()

    def test_limit_applied_to_candidates(self, tmp_path):
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        try:
            candidates = pd.DataFrame(
                [{"code": str(i).zfill(6)} for i in range(10)]
            )
            class Args:
                codes = None
                from_disclosures = False
                limit = 3
            codes = _resolve_codes(args=Args, store=store, period="2025A",
                                   candidates=candidates)
            assert len(codes) == 3
        finally:
            store.close()


class TestMergeFingerprints:
    def test_order_independent(self):
        a = {"consumer": "abc", "overseas": "xyz"}
        b = {"overseas": "xyz", "consumer": "abc"}
        assert _merge_fingerprints(a) == _merge_fingerprints(b)

    def test_single_strategy_no_pipe(self):
        assert _merge_fingerprints({"consumer": "abc"}) == "consumer:abc"


class TestConfigSchema:
    def test_scoring_enabled_by_default_args(self):
        class Args:
            non_em_max_workers = 2
            single_request_timeout = 30
            retry_times = 2
            resume = False
            enable_scoring = True

        cfg = _build_config_schema("overseas", "2025A", Args)
        assert cfg.score_weights is not None
        assert "growth" in cfg.score_weights

    def test_serialize_all_run_config_keeps_sub_strategy_configs(self):
        class Args:
            non_em_max_workers = 2
            single_request_timeout = 30
            retry_times = 2
            resume = False
            enable_scoring = True

        cfgs = {
            "consumer": _build_config_schema("consumer", "2025A", Args),
            "overseas": _build_config_schema("overseas", "2025A", Args),
        }
        payload = _serialize_run_config("all", cfgs)
        assert '"strategy": "all"' in payload
        assert '"consumer"' in payload
        assert '"overseas"' in payload
        assert '"score_weights"' in payload


class TestCoverageReport:
    def test_build_coverage_report_counts_fields_and_reasons(self):
        metrics = MetricsSchema()
        metrics.valuation.pe_ttm = 12.0
        metrics.growth.revenue_yoy = 0.2
        metrics.source_status.financials = "ok"
        metrics.source_status.valuation = "missing"
        metrics.score.final_score = 0.72
        metrics.score.coverage_ratio = 0.5
        result = ScreeningResult.watch(
            run_id="r1", code="600031", strategy="overseas", period="2025A",
            watch_reason="parse_warning", metrics=metrics,
        )
        coverage = _build_coverage_report([result])
        assert coverage["total"] == 1
        assert coverage["status_counts"] == {"watch": 1}
        assert coverage["reason_counts"]["watch_reason"]["parse_warning"] == 1
        assert coverage["metric_coverage"]["valuation.pe_ttm"]["coverage_ratio"] == 1.0
        assert coverage["metric_coverage"]["quality.debt_ratio"]["coverage_ratio"] == 0.0
        assert coverage["source_status_counts"]["valuation"] == {"missing": 1}
        assert coverage["score"]["avg_coverage_ratio"] == 0.5


class TestLoadSkipCodesForResume:
    """--resume 跳过逻辑：按 strategy + fingerprint 匹配上次 run。"""

    def _seed_prev_run(
        self, store: DuckDBStore, run_id: str, strategy: str,
        fp: str, status: str = "success",
        candidate_rows: list[dict] | None = None,
    ):
        store.conn.execute(
            "INSERT INTO screen_runs "
            "(run_id, strategy, period, report_type, started_at, finished_at, "
            " input_count, hit_count, watch_count, rejected_count, "
            " data_missing_count, error_count, config_json, config_fingerprint, "
            " status, error) "
            "VALUES (?, ?, '2025A', 'annual', CURRENT_TIMESTAMP - INTERVAL '2 hour', "
            "        CURRENT_TIMESTAMP - INTERVAL '1 hour', "
            "        5, 0, 0, 0, 0, 0, '{}', ?, ?, NULL)",
            [run_id, strategy, fp, status],
        )
        if candidate_rows:
            store.save_candidate_scores(candidate_rows)

    def test_all_strategy_finds_prev_all_run(self, tmp_path):
        """--strategy all 上次也跑 all → fingerprint 一致 → 跳过上次的 hit/watch/rejected。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        try:
            merged_fp = _merge_fingerprints({"consumer": "c1", "overseas": "o1"})
            self._seed_prev_run(
                store, "prev_run_1", strategy="all", fp=merged_fp,
                candidate_rows=[
                    {"run_id": "prev_run_1", "code": "600031", "name": "A",
                     "strategy": "consumer", "period": "2025A",
                     "status": "hit", "hit_reason": "all_thresholds_met",
                     "reject_reason": None, "data_missing_reason": None,
                     "metrics_json": "{}"},
                    {"run_id": "prev_run_1", "code": "600031", "name": "A",
                     "strategy": "overseas", "period": "2025A",
                     "status": "rejected", "reject_reason": "pe_ttm_too_high",
                     "hit_reason": None, "data_missing_reason": None,
                     "metrics_json": "{}"},
                    {"run_id": "prev_run_1", "code": "000001", "name": "B",
                     "strategy": "consumer", "period": "2025A",
                     "status": "data_missing",
                     "data_missing_reason": "pe_history_missing",
                     "hit_reason": None, "reject_reason": None,
                     "metrics_json": "{}"},
                ],
            )
            skip = _load_skip_codes_for_resume(
                store, period="2025A", strategy_arg="all",
                expected_fp=merged_fp,
            )
            # 600031 有 hit/consumer + rejected/overseas → 跳过
            # 000001 只有 data_missing → 不跳过（需重试）
            assert skip == {"600031"}
        finally:
            store.close()

    def test_fp_mismatch_no_skip(self, tmp_path):
        """fingerprint 不同 → 不跳过（上次配置已变）。"""
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        try:
            self._seed_prev_run(
                store, "prev_run_2", strategy="all", fp="old_fp",
                candidate_rows=[
                    {"run_id": "prev_run_2", "code": "600031", "name": "A",
                     "strategy": "consumer", "period": "2025A",
                     "status": "hit", "hit_reason": "x",
                     "reject_reason": None, "data_missing_reason": None,
                     "metrics_json": "{}"},
                ],
            )
            skip = _load_skip_codes_for_resume(
                store, period="2025A", strategy_arg="all",
                expected_fp="new_fp",
            )
            assert skip == set()
        finally:
            store.close()

    def test_strategy_mismatch_no_skip(self, tmp_path):
        """上次跑 all，本次跑 consumer-only → 查 strategy='consumer' 找不到 → 不跳过。

        合理语义：用户切换了策略范围，应当重跑。
        """
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        try:
            merged_fp = _merge_fingerprints({"consumer": "c1", "overseas": "o1"})
            self._seed_prev_run(
                store, "prev_run_3", strategy="all", fp=merged_fp,
                candidate_rows=[
                    {"run_id": "prev_run_3", "code": "600031", "name": "A",
                     "strategy": "consumer", "period": "2025A",
                     "status": "hit", "hit_reason": "x",
                     "reject_reason": None, "data_missing_reason": None,
                     "metrics_json": "{}"},
                ],
            )
            skip = _load_skip_codes_for_resume(
                store, period="2025A", strategy_arg="consumer",
                expected_fp="c1",
            )
            assert skip == set()
        finally:
            store.close()
