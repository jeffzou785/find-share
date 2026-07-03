"""screen_runs / candidate_scores 表和 disclosures 扩列迁移测试（P0-2 / P0-11）。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.screening.schemas import ConfigSchema, MetricsSchema, Thresholds
from src.storage import DuckDBStore


@pytest.fixture
def store(tmp_path: Path) -> DuckDBStore:
    db = DuckDBStore(db_path=tmp_path / "test.duckdb")
    yield db
    db.close()


class TestSchemaMigration:
    def test_disclosures_extended_columns_exist(self, store):
        rows = store.conn.execute("PRAGMA table_info(disclosures)").fetchall()
        cols = {r[1] for r in rows}
        assert "report_type" in cols
        assert "pdf_path" in cols
        assert "ingested_at" in cols
        assert "status" in cols
        assert "error" in cols

    def test_migration_idempotent(self, store):
        """多次初始化不应报错（CREATE TABLE IF NOT EXISTS + ADD COLUMN 幂等）。"""
        store._init_schema()
        store._init_schema()
        rows = store.conn.execute("PRAGMA table_info(disclosures)").fetchall()
        cols = [r[1] for r in rows]
        # 不应有重复列
        assert len(cols) == len(set(cols))

    def test_screen_runs_columns(self, store):
        rows = store.conn.execute("PRAGMA table_info(screen_runs)").fetchall()
        cols = {r[1] for r in rows}
        expected = {
            "run_id", "strategy", "period", "report_type",
            "started_at", "finished_at",
            "input_count", "hit_count", "watch_count",
            "rejected_count", "data_missing_count", "error_count",
            "config_json", "config_fingerprint", "coverage_json", "status", "error",
        }
        assert expected <= cols

    def test_candidate_scores_columns(self, store):
        rows = store.conn.execute("PRAGMA table_info(candidate_scores)").fetchall()
        cols = {r[1] for r in rows}
        expected = {
            "run_id", "code", "name", "strategy", "period", "status",
            "hit_reason", "reject_reason", "watch_reason",
            "data_missing_reason", "error", "metrics_json",
            "human_label", "label_reason", "labeled_at", "created_at",
        }
        assert expected <= cols

    def test_pharma_vbp_events_columns(self, store):
        rows = store.conn.execute("PRAGMA table_info(pharma_vbp_events)").fetchall()
        cols = {r[1] for r in rows}
        expected = {
            "code", "name", "product_name", "vbp_batch", "vbp_status",
            "tender_date", "province", "price_before", "price_after",
            "volume_commitment", "source", "source_url", "evidence_text",
            "updated_at",
        }
        assert expected <= cols

    def test_stock_industry_emweb_columns_preserved(self, store):
        rows = store.conn.execute("PRAGMA table_info(stock_industry)").fetchall()
        cols = {r[1] for r in rows}
        assert {"sw_second", "em2016", "csrc_industry", "csrc_section", "province"} <= cols

        store.save_stock_industry(pd.DataFrame([
            {
                "code": "600276",
                "name": "恒瑞医药",
                "sina_industry": "",
                "sw_first": "医药生物",
                "sw_second": "化学制剂",
                "em2016": "医药生物-化学制药-化学制剂",
                "csrc_industry": "医药制造业",
                "csrc_section": "制造业",
                "province": "江苏",
            }
        ]))
        row = store.load_stock_industry(sw_first=["医药生物"]).iloc[0]
        assert row["sw_second"] == "化学制剂"
        assert row["em2016"] == "医药生物-化学制药-化学制剂"

    def test_backtest_results_columns(self, store):
        rows = store.conn.execute("PRAGMA table_info(backtest_results)").fetchall()
        cols = {r[1] for r in rows}
        expected = {
            "run_id", "code", "name", "strategy", "period", "window_days",
            "anchor_date", "start_date", "end_date", "start_close", "end_close",
            "absolute_return", "benchmark_code", "benchmark_return",
            "relative_return", "status", "error", "created_at",
        }
        assert expected <= cols

    def test_financial_validation_results_columns(self, store):
        rows = store.conn.execute(
            "PRAGMA table_info(financial_validation_results)"
        ).fetchall()
        cols = {r[1] for r in rows}
        expected = {
            "run_id", "code", "name", "strategy", "candidate_status",
            "source_period", "validation_period", "validation_report_date",
            "verdict", "revenue_yoy", "net_profit_yoy",
            "deducted_net_profit", "gross_margin", "ocf_per_share",
            "checks_json", "error", "created_at",
        }
        assert expected <= cols

    def test_disclosures_period_to_report_type_backfill(self, store):
        """老库 disclosures 历史行应从 period 推导 report_type。"""
        df = pd.DataFrame(
            [
                {"code": "600031", "name": "X", "period": "2024A",
                 "first_schedule": None, "actual_date": None},
                {"code": "600032", "name": "Y", "period": "2024H",
                 "first_schedule": None, "actual_date": None},
            ]
        )
        store.upert_dataframe("disclosures", df)
        store._migrate()
        result = store.conn.execute(
            "SELECT code, report_type FROM disclosures ORDER BY code"
        ).fetchall()
        assert result == [("600031", "annual"), ("600032", "half_year")]


class TestScreenRun:
    def test_create_and_finish(self, store):
        store.create_screen_run(
            run_id="run1", strategy="overseas", period="2025A",
            report_type="annual",
            config_json="{}", config_fingerprint="fp1",
            input_count=10,
        )
        run = store.load_screen_run("run1").iloc[0]
        assert run["status"] == "running"
        assert run["input_count"] == 10
        assert pd.isna(run["finished_at"])

        store.finish_screen_run(
            "run1", status="success",
            counts={"hit": 3, "watch": 2, "rejected": 4, "data_missing": 1, "error": 0},
            coverage_json={"total": 10, "score": {"avg_coverage_ratio": 0.8}},
        )
        run = store.load_screen_run("run1").iloc[0]
        assert run["status"] == "success"
        assert run["hit_count"] == 3
        assert run["watch_count"] == 2
        assert run["rejected_count"] == 4
        assert run["data_missing_count"] == 1
        assert json.loads(run["coverage_json"])["score"]["avg_coverage_ratio"] == 0.8
        assert run["finished_at"] is not None

    def test_list_runs_orders_by_started_desc(self, store):
        store.create_screen_run("r1", "overseas", "2025A", "annual", "{}", "f1")
        store.create_screen_run("r2", "consumer", "2025A", "annual", "{}", "f2")
        listed = store.list_screen_runs(period="2025A")
        # r2 后创建，应排前面
        assert listed.iloc[0]["run_id"] == "r2"
        assert listed.iloc[1]["run_id"] == "r1"

    def test_cleanup_stale_runs(self, store):
        """超时 running 状态应被清理为 failed。"""
        store.create_screen_run("r1", "overseas", "2025A", "annual", "{}", "f")
        # 手动把 started_at 改到 2 小时前
        store.conn.execute(
            "UPDATE screen_runs SET started_at = CURRENT_TIMESTAMP - INTERVAL '2 HOUR' "
            "WHERE run_id = 'r1'"
        )
        n = store.cleanup_stale_screen_runs(max_age_hours=1)
        assert n == 1
        run = store.load_screen_run("r1").iloc[0]
        assert run["status"] == "failed"
        assert run["error"] == "process_killed_or_timeout"

    def test_cleanup_keeps_recent_running(self, store):
        store.create_screen_run("r1", "overseas", "2025A", "annual", "{}", "f")
        n = store.cleanup_stale_screen_runs(max_age_hours=1)
        assert n == 0
        run = store.load_screen_run("r1").iloc[0]
        assert run["status"] == "running"


class TestCandidateScores:
    def test_save_single(self, store):
        store.create_screen_run("r1", "overseas", "2025A", "annual", "{}", "f")
        metrics = MetricsSchema()
        metrics.valuation.pe_ttm = 20.0
        store.save_candidate_score(
            {
                "run_id": "r1", "code": "600031", "name": "三一重工",
                "strategy": "overseas", "period": "2025A", "status": "hit",
                "hit_reason": "all_thresholds_met",
                "metrics_json": metrics.to_json(),
            }
        )
        df = store.load_candidate_scores("r1")
        assert len(df) == 1
        row = df.iloc[0]
        assert row["code"] == "600031"
        assert row["status"] == "hit"
        parsed = json.loads(row["metrics_json"])
        assert parsed["valuation"]["pe_ttm"] == 20.0

    def test_save_batch(self, store):
        store.create_screen_run("r1", "overseas", "2025A", "annual", "{}", "f")
        rows = [
            {"run_id": "r1", "code": "A", "strategy": "overseas", "period": "2025A",
             "status": "hit"},
            {"run_id": "r1", "code": "B", "strategy": "overseas", "period": "2025A",
             "status": "rejected", "reject_reason": "overseas_ratio_too_low"},
            {"run_id": "r1", "code": "C", "strategy": "overseas", "period": "2025A",
             "status": "data_missing", "data_missing_reason": "pe_history_missing"},
        ]
        n = store.save_candidate_scores(rows)
        assert n == 3
        all_rows = store.load_candidate_scores("r1")
        assert len(all_rows) == 3
        statuses = set(all_rows["status"])
        assert statuses == {"hit", "rejected", "data_missing"}

    def test_save_watch_error_and_human_label_fields(self, store):
        store.create_screen_run("r1", "overseas", "2025A", "annual", "{}", "f")
        rows = [
            {"run_id": "r1", "code": "A", "strategy": "overseas", "period": "2025A",
             "status": "watch", "watch_reason": "parse_warning",
             "human_label": "watch", "label_reason": "unit ambiguous"},
            {"run_id": "r1", "code": "B", "strategy": "overseas", "period": "2025A",
             "status": "error", "error": "ValueError: bad pdf"},
        ]
        store.save_candidate_scores(rows)
        df = store.load_candidate_scores("r1")
        row_a = df[df["code"] == "A"].iloc[0]
        row_b = df[df["code"] == "B"].iloc[0]
        assert row_a["watch_reason"] == "parse_warning"
        assert row_a["human_label"] == "watch"
        assert row_a["label_reason"] == "unit ambiguous"
        assert row_b["error"] == "ValueError: bad pdf"

    def test_save_candidate_scores_preserves_existing_human_label(self, store):
        store.create_screen_run("r1", "overseas", "2025A", "annual", "{}", "f")
        store.save_candidate_scores([
            {"run_id": "r1", "code": "A", "strategy": "overseas", "period": "2025A",
             "status": "hit", "human_label": "hit", "label_reason": "baseline"},
        ])
        store.save_candidate_scores([
            {"run_id": "r1", "code": "A", "strategy": "overseas", "period": "2025A",
             "status": "watch", "watch_reason": "near_threshold"},
        ])
        row = store.load_candidate_scores("r1").iloc[0]
        assert row["status"] == "watch"
        assert row["watch_reason"] == "near_threshold"
        assert row["human_label"] == "hit"
        assert row["label_reason"] == "baseline"

    def test_update_candidate_label(self, store):
        store.create_screen_run("r1", "overseas", "2025A", "annual", "{}", "f")
        store.save_candidate_scores([
            {"run_id": "r1", "code": "A", "strategy": "overseas", "period": "2025A",
             "status": "hit"},
        ])
        assert store.update_candidate_label(
            run_id="r1", code="A", strategy="overseas",
            human_label="false_positive", label_reason="one-off gain",
        ) is True
        row = store.load_candidate_scores("r1").iloc[0]
        assert row["human_label"] == "false_positive"
        assert row["label_reason"] == "one-off gain"
        assert row["labeled_at"] is not None
        assert store.update_candidate_label(
            run_id="r1", code="missing", strategy="overseas",
            human_label="watch",
        ) is False

    def test_filter_by_status(self, store):
        store.create_screen_run("r1", "overseas", "2025A", "annual", "{}", "f")
        rows = [
            {"run_id": "r1", "code": "A", "strategy": "overseas", "period": "2025A",
             "status": "hit"},
            {"run_id": "r1", "code": "B", "strategy": "overseas", "period": "2025A",
             "status": "rejected"},
        ]
        store.save_candidate_scores(rows)
        only_hit = store.load_candidate_scores("r1", status="hit")
        assert len(only_hit) == 1
        assert only_hit.iloc[0]["code"] == "A"

    def test_upsert_on_same_pk(self, store):
        """同一 (run_id, code, strategy) 应 upsert，不重复。"""
        store.create_screen_run("r1", "overseas", "2025A", "annual", "{}", "f")
        store.save_candidate_score(
            {"run_id": "r1", "code": "A", "strategy": "overseas",
             "period": "2025A", "status": "data_missing"}
        )
        store.save_candidate_score(
            {"run_id": "r1", "code": "A", "strategy": "overseas",
             "period": "2025A", "status": "hit", "hit_reason": "all_met"}
        )
        df = store.load_candidate_scores("r1")
        assert len(df) == 1
        assert df.iloc[0]["status"] == "hit"
        assert df.iloc[0]["hit_reason"] == "all_met"

    def test_load_latest_for_strategy_period(self, store):
        """load_latest_candidate_scores 应只拿最近一次 run。"""
        store.create_screen_run("r1", "overseas", "2025A", "annual", "{}", "f")
        # 把 r1 的 started_at 调早
        store.conn.execute(
            "UPDATE screen_runs SET started_at = CURRENT_TIMESTAMP - INTERVAL '1 HOUR' "
            "WHERE run_id = 'r1'"
        )
        store.save_candidate_scores([
            {"run_id": "r1", "code": "OLD", "strategy": "overseas", "period": "2025A",
             "status": "hit"},
        ])

        store.create_screen_run("r2", "overseas", "2025A", "annual", "{}", "f")
        store.save_candidate_scores([
            {"run_id": "r2", "code": "NEW", "strategy": "overseas", "period": "2025A",
             "status": "hit"},
        ])

        df = store.load_latest_candidate_scores("overseas", "2025A")
        assert len(df) == 1
        assert df.iloc[0]["code"] == "NEW"


class TestPharmaVbpEvents:
    def test_save_and_load_pharma_vbp_events(self, store):
        df = pd.DataFrame([
            {
                "code": "600276",
                "name": "恒瑞医药",
                "product_name": "药品A",
                "vbp_batch": "第八批",
                "vbp_status": "won",
                "tender_date": "2024-01-01",
                "province": "全国",
                "price_before": 10.0,
                "price_after": 5.0,
                "volume_commitment": "约定采购量",
                "source": "manual",
                "source_url": "https://example.com",
                "evidence_text": "中选",
            }
        ])
        assert store.save_pharma_vbp_events(df) == 1
        loaded = store.load_pharma_vbp_events("600276")
        assert len(loaded) == 1
        row = loaded.iloc[0]
        assert row["product_name"] == "药品A"
        assert row["vbp_status"] == "won"


class TestBacktestResults:
    def test_save_and_load_backtest_results(self, store):
        df = pd.DataFrame([
            {
                "run_id": "r1",
                "code": "600276",
                "name": "恒瑞医药",
                "strategy": "pharma",
                "period": "2025A",
                "window_days": 20,
                "anchor_date": "2026-01-01",
                "start_date": "2026-01-02",
                "end_date": "2026-02-01",
                "start_close": 10.0,
                "end_close": 12.0,
                "absolute_return": 0.2,
                "benchmark_code": "000300",
                "benchmark_return": 0.1,
                "relative_return": 0.1,
                "status": "ok",
            }
        ])
        assert store.save_backtest_results(df) == 1
        loaded = store.load_backtest_results("r1")
        assert len(loaded) == 1
        row = loaded.iloc[0]
        assert row["code"] == "600276"
        assert row["window_days"] == 20
        assert row["relative_return"] == pytest.approx(0.1)

        df.loc[0, "relative_return"] = 0.15
        assert store.save_backtest_results(df) == 1
        loaded = store.load_backtest_results("r1")
        assert len(loaded) == 1
        assert loaded.iloc[0]["relative_return"] == pytest.approx(0.15)


class TestFinancialValidationResults:
    def test_save_and_load_financial_validation_results(self, store):
        df = pd.DataFrame([
            {
                "run_id": "r1",
                "code": "600031",
                "name": "三一重工",
                "strategy": "overseas",
                "candidate_status": "hit",
                "source_period": "2025A",
                "validation_period": "2026Q1",
                "validation_report_date": "2026-03-31",
                "verdict": "confirmed",
                "revenue_yoy": 0.12,
                "net_profit_yoy": 0.08,
                "deducted_net_profit": 100.0,
                "gross_margin": 0.3,
                "ocf_per_share": 0.5,
                "checks_json": "{}",
            }
        ])
        assert store.save_financial_validation_results(df) == 1
        loaded = store.load_financial_validation_results("r1")
        assert len(loaded) == 1
        row = loaded.iloc[0]
        assert row["code"] == "600031"
        assert row["validation_period"] == "2026Q1"
        assert row["revenue_yoy"] == pytest.approx(0.12)

        df.loc[0, "verdict"] = "mixed"
        assert store.save_financial_validation_results(df) == 1
        loaded = store.load_financial_validation_results("r1")
        assert len(loaded) == 1
        assert loaded.iloc[0]["verdict"] == "mixed"
