"""P2-3 run_diff 动态监控测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.screening.run_diff import (
    DEFAULT_METRIC_THRESHOLDS,
    DiffEvent,
    RunDiff,
    _diff_metrics,
    _get_nested,
    _parse_metrics,
    diff_latest_two_runs,
    diff_runs,
)
from src.storage import DuckDBStore


def _metrics_json(**overrides) -> str:
    """构造 metrics_json 字符串，可指定顶层字段。"""
    base = {
        "valuation": {"pe_ttm": None, "pe_pct_5y": None},
        "growth": {"deducted_profit_yoy_ttm": None, "revenue_yoy": None},
        "quality": {"gross_margin": None, "debt_ratio": None},
        "overseas": {"overseas_ratio": None, "overseas_yoy": None, "parse_warning": None},
        "catalyst": {"reports_count_90d": None, "news_count_30d": None},
        "source_status": {"financials": "ok"},
        "score": {"final_score": None},
    }
    for k, v in overrides.items():
        if "." in k:
            top, sub = k.split(".", 1)
            base.setdefault(top, {})[sub] = v
        else:
            base[k] = v
    return json.dumps(base, ensure_ascii=False)


@pytest.fixture
def store(tmp_path: Path):
    s = DuckDBStore(db_path=tmp_path / "t.duckdb")
    yield s
    s.close()


def _seed_run(
    store: DuckDBStore,
    run_id: str,
    strategy: str,
    period: str,
    rows: list[dict],
) -> None:
    """创建一个 screen_run + 关联 candidate_scores。"""
    store.create_screen_run(
        run_id=run_id, strategy=strategy, period=period,
        report_type="annual", config_json="{}", config_fingerprint="fp",
        input_count=len(rows),
    )
    store.save_candidate_scores(rows)
    store.finish_screen_run(run_id, "success")


class TestHelpers:
    def test_parse_metrics_empty(self):
        assert _parse_metrics(None) == {}
        assert _parse_metrics("") == {}
        assert _parse_metrics("garbage") == {}

    def test_parse_metrics_ok(self):
        d = _parse_metrics('{"valuation": {"pe_ttm": 25.0}}')
        assert d["valuation"]["pe_ttm"] == 25.0

    def test_get_nested(self):
        d = {"a": {"b": {"c": 1}}}
        assert _get_nested(d, "a.b.c") == 1
        assert _get_nested(d, "a.b.x") is None
        assert _get_nested(d, "a.x.y") is None
        assert _get_nested({}, "x") is None

    def test_diff_metrics_skips_none(self):
        before = {"valuation": {"pe_ttm": None}}
        after = {"valuation": {"pe_ttm": None}}
        assert _diff_metrics(before, after, DEFAULT_METRIC_THRESHOLDS) == []

    def test_diff_metrics_below_threshold(self):
        before = {"valuation": {"pe_ttm": 20.0}}
        after = {"valuation": {"pe_ttm": 22.0}}
        # PE 阈值 5.0，差 2 < 5 → 不报
        assert _diff_metrics(before, after, DEFAULT_METRIC_THRESHOLDS) == []

    def test_diff_metrics_above_threshold(self):
        before = {"valuation": {"pe_ttm": 20.0}}
        after = {"valuation": {"pe_ttm": 30.0}}
        out = _diff_metrics(before, after, DEFAULT_METRIC_THRESHOLDS)
        assert len(out) == 1
        assert out[0][0] == "valuation.pe_ttm"

    def test_diff_metrics_none_to_value(self):
        before = {"valuation": {"pe_ttm": None}}
        after = {"valuation": {"pe_ttm": 30.0}}
        out = _diff_metrics(before, after, DEFAULT_METRIC_THRESHOLDS)
        assert len(out) == 1


class TestDiffRuns:
    def test_new_hit_detected(self, store):
        _seed_run(store, "r1", "overseas", "2025A", [
            {"run_id": "r1", "code": "600031", "name": "三一",
             "strategy": "overseas", "period": "2025A",
             "status": "rejected", "reject_reason": "pe_ttm_too_high",
             "data_missing_reason": None,
             "metrics_json": _metrics_json()},
        ])
        _seed_run(store, "r2", "overseas", "2025A", [
            {"run_id": "r2", "code": "600031", "name": "三一",
             "strategy": "overseas", "period": "2025A",
             "status": "hit", "hit_reason": "all_thresholds_met",
             "data_missing_reason": None,
             "metrics_json": _metrics_json()},
        ])
        diff = diff_runs(store, "r1", "r2")
        assert len(diff.new_hits) == 1
        assert diff.new_hits[0].code == "600031"

    def test_dropped_hit_detected(self, store):
        _seed_run(store, "r1", "overseas", "2025A", [
            {"run_id": "r1", "code": "600031", "name": "三一",
             "strategy": "overseas", "period": "2025A",
             "status": "hit", "hit_reason": "all_thresholds_met",
             "data_missing_reason": None,
             "metrics_json": _metrics_json()},
        ])
        _seed_run(store, "r2", "overseas", "2025A", [
            {"run_id": "r2", "code": "600031", "name": "三一",
             "strategy": "overseas", "period": "2025A",
             "status": "rejected", "reject_reason": "pe_ttm_too_high",
             "data_missing_reason": None,
             "metrics_json": _metrics_json()},
        ])
        diff = diff_runs(store, "r1", "r2")
        assert len(diff.dropped_hits) == 1
        assert diff.dropped_hits[0].code == "600031"

    def test_metric_change_detected(self, store):
        _seed_run(store, "r1", "overseas", "2025A", [
            {"run_id": "r1", "code": "600031", "name": "三一",
             "strategy": "overseas", "period": "2025A",
             "status": "hit", "hit_reason": "all_thresholds_met",
             "data_missing_reason": None,
             "metrics_json": _metrics_json(**{"valuation.pe_ttm": 15.0})},
        ])
        _seed_run(store, "r2", "overseas", "2025A", [
            {"run_id": "r2", "code": "600031", "name": "三一",
             "strategy": "overseas", "period": "2025A",
             "status": "hit", "hit_reason": "all_thresholds_met",
             "data_missing_reason": None,
             "metrics_json": _metrics_json(**{"valuation.pe_ttm": 30.0})},
        ])
        diff = diff_runs(store, "r1", "r2")
        # PE 从 15 → 30 差 15 > 阈值 5
        assert any(
            e.kind == "metric_changed"
            and e.metric_key == "valuation.pe_ttm"
            for e in diff.events
        )

    def test_new_parse_warning_detected(self, store):
        _seed_run(store, "r1", "overseas", "2025A", [
            {"run_id": "r1", "code": "600031", "name": "三一",
             "strategy": "overseas", "period": "2025A",
             "status": "hit", "hit_reason": "all_thresholds_met",
             "data_missing_reason": None,
             "metrics_json": _metrics_json()},
        ])
        _seed_run(store, "r2", "overseas", "2025A", [
            {"run_id": "r2", "code": "600031", "name": "三一",
             "strategy": "overseas", "period": "2025A",
             "status": "watch", "watch_reason": "parse_warning",
             "data_missing_reason": None,
             "metrics_json": _metrics_json(**{"overseas.parse_warning": "unit_ambiguous"})},
        ])
        diff = diff_runs(store, "r1", "r2")
        # 同时触发 dropped_hit + new_parse_warning + status_changed
        kinds = {e.kind for e in diff.events}
        assert "dropped_hit" in kinds
        assert "new_parse_warning" in kinds

    def test_newly_added_code(self, store):
        """新 run 里多了一只股票（旧 run 没有）→ new_hit。"""
        _seed_run(store, "r1", "overseas", "2025A", [])
        _seed_run(store, "r2", "overseas", "2025A", [
            {"run_id": "r2", "code": "600031", "name": "三一",
             "strategy": "overseas", "period": "2025A",
             "status": "hit", "hit_reason": "all_thresholds_met",
             "data_missing_reason": None,
             "metrics_json": _metrics_json()},
        ])
        diff = diff_runs(store, "r1", "r2")
        assert len(diff.new_hits) == 1

    def test_removed_code(self, store):
        """新 run 里少了一只股票（旧 run 有）→ dropped_hit。"""
        _seed_run(store, "r1", "overseas", "2025A", [
            {"run_id": "r1", "code": "600031", "name": "三一",
             "strategy": "overseas", "period": "2025A",
             "status": "hit", "hit_reason": "all_thresholds_met",
             "data_missing_reason": None,
             "metrics_json": _metrics_json()},
        ])
        _seed_run(store, "r2", "overseas", "2025A", [])
        diff = diff_runs(store, "r1", "r2")
        assert len(diff.dropped_hits) == 1

    def test_no_change_returns_empty_events(self, store):
        """两次 run 内容一致 → events 为空。"""
        row = {"run_id": "r1", "code": "600031", "name": "三一",
               "strategy": "overseas", "period": "2025A",
               "status": "hit", "hit_reason": "all_thresholds_met",
               "data_missing_reason": None,
               "metrics_json": _metrics_json(**{"valuation.pe_ttm": 20.0})}
        _seed_run(store, "r1", "overseas", "2025A", [row])
        row2 = dict(row, run_id="r2")
        _seed_run(store, "r2", "overseas", "2025A", [row2])
        diff = diff_runs(store, "r1", "r2")
        assert diff.events == []

    def test_to_markdown_renders(self, store):
        _seed_run(store, "r1", "overseas", "2025A", [
            {"run_id": "r1", "code": "600031", "name": "三一",
             "strategy": "overseas", "period": "2025A",
             "status": "rejected", "reject_reason": "pe_ttm_too_high",
             "data_missing_reason": None,
             "metrics_json": _metrics_json()},
        ])
        _seed_run(store, "r2", "overseas", "2025A", [
            {"run_id": "r2", "code": "600031", "name": "三一",
             "strategy": "overseas", "period": "2025A",
             "status": "hit", "hit_reason": "all_thresholds_met",
             "data_missing_reason": None,
             "metrics_json": _metrics_json()},
        ])
        diff = diff_runs(store, "r1", "r2")
        md = diff.to_markdown()
        assert "新进入 hit" in md
        assert "600031" in md


class TestDiffLatestTwoRuns:
    def test_insufficient_runs_returns_none(self, store):
        _seed_run(store, "r1", "overseas", "2025A", [])
        assert diff_latest_two_runs(store, strategy="overseas", period="2025A") is None

    def test_picks_latest_two(self, store):
        _seed_run(store, "r1", "overseas", "2025A", [
            {"run_id": "r1", "code": "600031", "name": "三一",
             "strategy": "overseas", "period": "2025A",
             "status": "rejected", "reject_reason": "pe_ttm_too_high",
             "data_missing_reason": None,
             "metrics_json": _metrics_json()},
        ])
        _seed_run(store, "r2", "overseas", "2025A", [
            {"run_id": "r2", "code": "600031", "name": "三一",
             "strategy": "overseas", "period": "2025A",
             "status": "hit", "hit_reason": "all_thresholds_met",
             "data_missing_reason": None,
             "metrics_json": _metrics_json()},
        ])
        diff = diff_latest_two_runs(store, strategy="overseas", period="2025A")
        assert diff is not None
        assert len(diff.new_hits) == 1
