"""状态语义和 ScreeningResult 工厂方法测试（P0-5 / P0-6）。"""
from __future__ import annotations

import json

import pytest

from src.screening import (
    DATA_MISSING_REASONS,
    REJECT_CONSUMER,
    REJECT_OVERSEAS,
    WATCH_REASONS,
    MetricsSchema,
    ScreeningResult,
    Status,
)
from src.screening.status import validate_reason_codes


class TestStatusEnum:
    def test_values(self):
        assert Status.HIT.value == "hit"
        assert Status.WATCH.value == "watch"
        assert Status.REJECTED.value == "rejected"
        assert Status.DATA_MISSING.value == "data_missing"
        assert Status.ERROR.value == "error"

    def test_str_comparison(self):
        """Status 继承 str，可直接和字符串比较。"""
        assert Status.HIT == "hit"
        assert Status.REJECTED == "rejected"


class TestReasonCodes:
    def test_consumer_reject_codes_complete(self):
        expected = {
            "not_target_consumer_industry",
            "pe_history_missing",
            "pe_percentile_too_high",
            "deducted_profit_missing",
            "deducted_yoy_too_low",
            "not_inflection_or_trend",
            "market_cap_too_small",
            "st_or_delisting",
            # P1-1 新增
            "pb_percentile_too_high",
            "revenue_yoy_too_low",
            "gross_margin_deteriorating",
        }
        assert REJECT_CONSUMER == expected

    def test_overseas_reject_codes_complete(self):
        expected = {
            "not_target_manufacturing_industry",
            "overseas_revenue_missing",
            "overseas_ratio_too_low",
            "overseas_ratio_abnormal",
            "overseas_yoy_abnormal",
            "pe_ttm_too_high",
            "cashflow_quality_failed",
            "debt_ratio_too_high",
            "financial_data_missing",
        }
        assert REJECT_OVERSEAS == expected

    def test_watch_reasons_include_parse_warning(self):
        assert "parse_warning" in WATCH_REASONS
        assert "consensus_missing" in WATCH_REASONS
        assert "near_threshold" in WATCH_REASONS

    def test_data_missing_reasons_include_pdf(self):
        assert "pdf_not_downloaded" in DATA_MISSING_REASONS
        assert "pe_history_empty" in DATA_MISSING_REASONS

    def test_validate_reason_codes_returns_all(self):
        v = validate_reason_codes()
        assert set(v.keys()) == {
            "reject_consumer", "reject_overseas", "watch", "data_missing"
        }


class TestScreeningResultFactories:
    def _base_kwargs(self, status: Status):
        return dict(
            run_id="r1", code="600031", strategy="overseas", period="2025A",
        )

    def test_hit_factory(self):
        r = ScreeningResult.hit(
            **self._base_kwargs(Status.HIT),
            hit_reason="all_thresholds_met",
            name="三一重工",
        )
        assert r.status == Status.HIT
        assert r.hit_reason == "all_thresholds_met"
        assert r.name == "三一重工"

    def test_rejected_factory(self):
        r = ScreeningResult.rejected(
            **self._base_kwargs(Status.REJECTED),
            reject_reason="overseas_ratio_too_low",
        )
        assert r.status == Status.REJECTED
        assert r.reject_reason == "overseas_ratio_too_low"

    def test_watch_factory(self):
        r = ScreeningResult.watch(
            **self._base_kwargs(Status.WATCH),
            watch_reason="parse_warning",
        )
        assert r.status == Status.WATCH
        assert r.watch_reason == "parse_warning"

    def test_data_missing_factory(self):
        r = ScreeningResult.data_missing(
            **self._base_kwargs(Status.DATA_MISSING),
            data_missing_reason="pe_history_empty",
        )
        assert r.status == Status.DATA_MISSING
        assert r.data_missing_reason == "pe_history_empty"

    def test_from_exception_factory(self):
        r = ScreeningResult.from_exception(
            **self._base_kwargs(Status.ERROR),
            error="ValueError: bad pdf",
        )
        assert r.status == Status.ERROR
        assert "ValueError" in r.error


class TestScreeningResultConsistency:
    def test_hit_requires_hit_reason(self):
        with pytest.raises(ValueError, match="hit_reason"):
            ScreeningResult(
                run_id="r1", code="X", strategy="overseas", period="2025A",
                status=Status.HIT,
            )

    def test_rejected_requires_reject_reason(self):
        with pytest.raises(ValueError, match="reject_reason"):
            ScreeningResult(
                run_id="r1", code="X", strategy="overseas", period="2025A",
                status=Status.REJECTED,
            )

    def test_watch_requires_watch_reason(self):
        with pytest.raises(ValueError, match="watch_reason"):
            ScreeningResult(
                run_id="r1", code="X", strategy="overseas", period="2025A",
                status=Status.WATCH,
            )

    def test_data_missing_requires_reason(self):
        with pytest.raises(ValueError, match="data_missing_reason"):
            ScreeningResult(
                run_id="r1", code="X", strategy="overseas", period="2025A",
                status=Status.DATA_MISSING,
            )

    def test_error_requires_error_message(self):
        with pytest.raises(ValueError, match="error"):
            ScreeningResult(
                run_id="r1", code="X", strategy="overseas", period="2025A",
                status=Status.ERROR,
            )

    def test_watch_with_parse_warning_ok(self):
        """parse_warning 应该可以进入 watch（不强制 rejected）。"""
        r = ScreeningResult.watch(
            run_id="r1", code="X", strategy="overseas", period="2025A",
            watch_reason="parse_warning",
        )
        assert r.status == Status.WATCH


class TestScreeningResultToRow:
    def test_to_row_status_is_string(self):
        r = ScreeningResult.hit(
            run_id="r1", code="X", strategy="overseas", period="2025A",
            hit_reason="all_met",
        )
        row = r.to_row()
        assert row["status"] == "hit"
        assert row["hit_reason"] == "all_met"
        assert isinstance(row["metrics_json"], str)

    def test_to_row_metrics_json_serializable(self):
        m = MetricsSchema()
        m.valuation.pe_ttm = 25.0
        m.overseas.parse_warning = "unit_ambiguous"
        r = ScreeningResult.watch(
            run_id="r1", code="X", strategy="overseas", period="2025A",
            watch_reason="parse_warning", metrics=m,
        )
        row = r.to_row()
        parsed = json.loads(row["metrics_json"])
        assert parsed["valuation"]["pe_ttm"] == 25.0
        assert parsed["overseas"]["parse_warning"] == "unit_ambiguous"

    def test_to_row_can_be_saved_to_db(self, tmp_path):
        from src.storage import DuckDBStore

        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        store.create_screen_run("r1", "overseas", "2025A", "annual", "{}", "f")
        r = ScreeningResult.hit(
            run_id="r1", code="600031", name="三一重工",
            strategy="overseas", period="2025A",
            hit_reason="all_thresholds_met",
        )
        store.save_candidate_score(r.to_row())
        loaded = store.load_candidate_scores("r1").iloc[0]
        assert loaded["code"] == "600031"
        assert loaded["status"] == "hit"
        assert loaded["name"] == "三一重工"
        store.close()
