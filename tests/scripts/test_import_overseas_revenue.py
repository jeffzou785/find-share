"""P1-3 import_overseas_revenue.py 单元测试。

覆盖：
- _cross_year_check：多年金额序列异常检测（>100x 或 <1/100x）
- _load_history_from_store：从 DuckDB 读历史数据
- _record_to_candidate_dict：候选序列化
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import pytest

# import_overseas_revenue.py 是脚本，不在 src 包内；通过 importlib 加载
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "import_overseas_revenue",
    PROJECT_ROOT / "scripts" / "import_overseas_revenue.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_cross_year_check = _mod._cross_year_check
_load_history_from_store = _mod._load_history_from_store
_log = _mod._log
_record_to_candidate_dict = _mod._record_to_candidate_dict
CROSS_YEAR_FACTOR = _mod.CROSS_YEAR_FACTOR

from src.collectors.annual_report_parser import OverseasRevenueRecord
from src.storage import DuckDBStore


class TestLogging:
    def test_log_uses_logger(self, caplog):
        caplog.set_level("INFO")
        _log("hello")
        assert "hello" in caplog.text


class TestCrossYearCheck:
    def test_no_history_no_warning(self):
        warns = _cross_year_check("600519", 2025, 1e9, history={})
        assert warns == []

    def test_normal_growth_no_warning(self):
        # 同比增长 50% → 无 warning
        warns = _cross_year_check("600519", 2025, 1.5e9, history={2024: 1e9})
        assert warns == []

    def test_huge_jump_triggers_warning(self):
        # N 年比 N-1 年大 1000 倍 → 单位识别疑点
        warns = _cross_year_check(
            "600262", 2025, 1e9, history={2024: 1e6}
        )
        assert len(warns) == 1
        assert "cross_year_unit_anomaly" in warns[0]

    def test_huge_drop_triggers_warning(self):
        # N 年比 N-1 年小 1000 倍 → 单位识别疑点
        warns = _cross_year_check(
            "001333", 2025, 1e6, history={2024: 1e9}
        )
        assert len(warns) == 1
        assert "cross_year_unit_anomaly" in warns[0]

    def test_boundary_99x_no_warning(self):
        # 99 倍在阈值之下，不触发
        warns = _cross_year_check(
            "600519", 2025, 99e6, history={2024: 1e6}
        )
        assert warns == []

    def test_prev_year_zero_no_warning(self):
        # prev_year=0 → 不做校验（避免除零）
        warns = _cross_year_check(
            "600519", 2025, 1e9, history={2024: 0}
        )
        assert warns == []


class TestLoadHistoryFromStore:
    def test_empty_store_returns_empty_dict(self, tmp_path: Path):
        store = DuckDBStore(db_path=tmp_path / "test.duckdb")
        try:
            assert _load_history_from_store(store) == {}
        finally:
            store.close()

    def test_loads_existing_overseas_revenue(self, tmp_path: Path):
        store = DuckDBStore(db_path=tmp_path / "test.duckdb")
        try:
            # 预填 2 条历史记录
            df = pd.DataFrame([
                {"stock_code": "600519", "report_year": 2024,
                 "region_name": "境外", "revenue": 12.5, "revenue_unit": "亿元",
                 "source_page": 1, "raw_text": "境外 12.5亿",
                 "pdf_path": "/tmp/x.pdf"},
                {"stock_code": "600519", "report_year": 2023,
                 "region_name": "境外", "revenue": 10.0, "revenue_unit": "亿元",
                 "source_page": 1, "raw_text": "境外 10亿",
                 "pdf_path": "/tmp/x.pdf"},
            ])
            store.save_overseas_revenue(df.to_dict("records"))
            history = _load_history_from_store(store)
            assert "600519" in history
            assert history["600519"][2024] == 12.5 * 1e8
            assert history["600519"][2023] == 10.0 * 1e8
        finally:
            store.close()


class TestRecordToCandidateDict:
    def test_round_trip_keys(self):
        r = OverseasRevenueRecord(
            stock_code="600519", report_period="2024年报",
            region_name="境外", revenue=12.5, revenue_unit="亿元",
            revenue_yuan=12.5e8, source_page=42, raw_text="境外 12.5 亿",
            is_total_row=False, confidence="high",
        )
        d = _record_to_candidate_dict(r)
        assert d["region_name"] == "境外"
        assert d["revenue"] == 12.5
        assert d["revenue_unit"] == "亿元"
        assert d["revenue_yuan"] == 12.5e8
        assert d["is_total_row"] is False
        assert d["confidence"] == "high"
