"""P2-4 财报 vs 研报一致性校验测试。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.screening.consistency import (
    EPS_TOLERANCE,
    SEVERITY_INFO,
    SEVERITY_WARN,
    check_consistency,
    check_eps_consistency,
    check_overseas_consistency,
)
from src.storage import DuckDBStore


@pytest.fixture
def store(tmp_path: Path):
    s = DuckDBStore(db_path=tmp_path / "t.duckdb")
    yield s
    s.close()


def _save_eps(store: DuckDBStore, code: str, year: int, eps: float):
    df = pd.DataFrame([
        {"code": code, "report_date": pd.Timestamp(year=year, month=12, day=31),
         "statement_type": "lrb", "item_cn": "基本每股收益",
         "item_en": "eps_basic", "value": eps, "value_yoy": None,
         "updated_at": pd.Timestamp.now()},
    ])
    store.save_financials_full(df)


def _save_reports(store: DuckDBStore, code: str, reports: list[dict]):
    """reports: list of {publish_date, title, eps_forecast_y1, ...}"""
    rows = []
    for i, r in enumerate(reports):
        rows.append({
            "code": code, "stock_name": r.get("name", ""),
            "title": r.get("title", ""),
            "broker": r.get("broker", ""),
            "broker_code": "",
            "rating": r.get("rating", ""),
            "rating_prev": "", "rating_idx": 0.0,
            "eps_forecast_y1": r.get("eps_forecast_y1"),
            "eps_forecast_y2": r.get("eps_forecast_y2"),
            "eps_forecast_y3": None,
            "pe_forecast_y1": None, "pe_forecast_y2": None, "pe_forecast_y3": None,
            "industry": "",
            "publish_date": r.get("publish_date"),
            "research_date": None,
            "info_code": "",
            "report_id": f"rpt_{code}_{i}",
            "pdf_path": r.get("pdf_path", ""),
            "ingested_to_rag": False,
        })
    df = pd.DataFrame(rows)
    store.save_broker_reports(df)


def _save_overseas(store: DuckDBStore, code: str, year: int, revenue_yi: float):
    store.save_overseas_revenue([
        {"stock_code": code, "report_year": year, "region_name": "境外",
         "revenue": revenue_yi, "revenue_unit": "亿元",
         "source_page": 1, "raw_text": "", "pdf_path": ""},
    ])


class TestCheckEpsConsistency:
    def test_no_data_no_observation(self, store):
        """无任何数据时不产生 observation。"""
        obs = check_eps_consistency(store, "600031", 2025)
        assert obs == []

    def test_actual_only_no_observation(self, store):
        _save_eps(store, "600031", 2025, 2.0)
        obs = check_eps_consistency(store, "600031", 2025)
        assert obs == []

    def test_deviation_within_tolerance(self, store):
        _save_eps(store, "600031", 2025, 2.0)
        _save_reports(store, "600031", [
            {"publish_date": pd.Timestamp.now(), "eps_forecast_y1": 2.2,
             "title": "Review"},
        ])
        obs = check_eps_consistency(store, "600031", 2025)
        assert len(obs) == 1
        assert obs[0].severity == SEVERITY_INFO

    def test_deviation_exceeds_tolerance(self, store):
        _save_eps(store, "600031", 2025, 2.0)
        _save_reports(store, "600031", [
            {"publish_date": pd.Timestamp.now(), "eps_forecast_y1": 3.5,
             "title": "Review"},
        ])
        # 偏差 (3.5 - 2.0)/2.0 = 75% > 25% → warn
        obs = check_eps_consistency(store, "600031", 2025)
        assert len(obs) == 1
        assert obs[0].severity == SEVERITY_WARN
        assert "偏差" in obs[0].message


class TestCheckOverseasConsistency:
    def test_overseas_no_reports_warns(self, store):
        """财报有海外收入但无研报 → warn（被忽视信号）。"""
        _save_overseas(store, "600031", 2025, 50.0)
        obs = check_overseas_consistency(store, "600031", 2025)
        assert len(obs) == 1
        assert obs[0].severity == SEVERITY_WARN

    def test_overseas_with_matching_reports(self, store):
        _save_overseas(store, "600031", 2025, 50.0)
        _save_reports(store, "600031", [
            {"publish_date": pd.Timestamp.now(),
             "title": "海外订单大增，欧洲市场突破"},
        ])
        obs = check_overseas_consistency(store, "600031", 2025)
        assert len(obs) == 1
        assert obs[0].severity == SEVERITY_INFO
        assert "海外" in obs[0].message or "欧洲" in obs[0].message

    def test_overseas_reports_without_keyword(self, store):
        """研报不提海外但财报披露海外 → warn。"""
        _save_overseas(store, "600031", 2025, 50.0)
        _save_reports(store, "600031", [
            {"publish_date": pd.Timestamp.now(),
             "title": "稳健经营，分红稳定"},
        ])
        obs = check_overseas_consistency(store, "600031", 2025)
        assert len(obs) == 1
        assert obs[0].severity == SEVERITY_WARN

    def test_no_overseas_data_no_observation(self, store):
        obs = check_overseas_consistency(store, "600031", 2025)
        assert obs == []


class TestCheckConsistency:
    def test_combined_eps_and_overseas(self, store):
        _save_eps(store, "600031", 2025, 2.0)
        _save_overseas(store, "600031", 2025, 50.0)
        _save_reports(store, "600031", [
            {"publish_date": pd.Timestamp.now(),
             "title": "海外业务高增",
             "eps_forecast_y1": 2.1},
        ])
        result = check_consistency(store, "600031", 2025)
        assert len(result.observations) >= 2
        kinds = {o.kind for o in result.observations}
        assert "eps_deviation" in kinds
        assert any("overseas" in k or "match" in k for k in kinds)

    def test_has_warning_property(self, store):
        _save_overseas(store, "600031", 2025, 50.0)
        # 无研报 → warn
        result = check_consistency(store, "600031", 2025)
        assert result.has_warning is True

    def test_to_dict_serializable(self, store):
        _save_eps(store, "600031", 2025, 2.0)
        result = check_consistency(store, "600031", 2025)
        d = result.to_dict()
        assert d["code"] == "600031"
        assert isinstance(d["observations"], list)
