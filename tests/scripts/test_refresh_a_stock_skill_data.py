"""refresh_a_stock_skill_data 脚本辅助函数测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts import refresh_a_stock_skill_data as mod


def test_validate_years_rejects_2023():
    with pytest.raises(ValueError, match="不处理 2023"):
        mod._validate_years([2023, 2024])


def test_validate_years_dedups_and_sorts():
    assert mod._validate_years([2025, 2024, 2025]) == [2024, 2025]


def test_validate_q1_year_rejects_2023():
    with pytest.raises(ValueError, match="不处理 2023Q1"):
        mod._validate_q1_year(2023)


def test_validate_q1_year_allows_2026_and_zero_skip():
    assert mod._validate_q1_year(2026) == 2026
    assert mod._validate_q1_year(0) is None


def test_normalize_codes_handles_market_prefix():
    assert mod._normalize_codes(["SH600519", "sz000001", "19"]) == [
        "600519",
        "000001",
        "000019",
    ]


def test_codes_from_run_reads_data_missing(tmp_path: Path, monkeypatch):
    run_id = "r1"
    run_dir = tmp_path / "data" / "exports" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "data_missing.csv").write_text(
        "code,strategy,data_missing_reason\n"
        "19,consumer,pe_history_missing\n"
        "000420,overseas,overseas_revenue_missing\n"
        "000420,overseas,overseas_revenue_missing\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)

    assert mod._codes_from_run(run_id) == ["000019", "000420"]
