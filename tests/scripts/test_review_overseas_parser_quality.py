"""策略三海外收入 parser 质量池测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.review_overseas_parser_quality import (
    build_quality_review,
    find_annual_report_pdfs,
    write_review,
)
from src.storage import DuckDBStore


def test_find_annual_report_pdfs_prefers_canonical(tmp_path: Path):
    canonical = tmp_path / "600031_2025_annual_report.pdf"
    legacy = tmp_path / "600031_2025_annual.pdf"
    other = tmp_path / "000001_2025_annual.pdf"
    canonical.touch()
    legacy.touch()
    other.touch()

    found = find_annual_report_pdfs(tmp_path, 2025)

    assert found["600031"] == canonical
    assert found["000001"] == other


def test_build_quality_review_merges_parser_and_screen_issues(tmp_path: Path):
    store = DuckDBStore(db_path=tmp_path / "t.duckdb")
    try:
        (tmp_path / "600031_2025_annual_report.pdf").touch()
        (tmp_path / "000001_2025_annual_report.pdf").touch()
        (tmp_path / "000002_2025_annual_report.pdf").touch()
        store.save_overseas_revenue([
            {
                "stock_code": "600031",
                "report_year": 2025,
                "region_name": "境外",
                "revenue": 12.3,
                "revenue_unit": "亿元",
                "source_page": 1,
                "raw_text": "境外收入 12.3 亿元",
                "pdf_path": "data/pdfs/annual_reports/600031_2025_annual_report.pdf",
                "candidates_json": json.dumps([{"region_name": "境外"}]),
                "parse_warning": "unit_ambiguous",
                "confidence": "high",
            },
            {
                "stock_code": "000001",
                "report_year": 2025,
                "region_name": "海外",
                "revenue": 5.0,
                "revenue_unit": "亿元",
                "source_page": 2,
                "raw_text": "海外收入 5 亿元",
                "pdf_path": "data/pdfs/annual_reports/000001_2025_annual_report.pdf",
                "candidates_json": "[]",
                "parse_warning": None,
                "confidence": "medium",
            },
        ])
        store.create_screen_run(
            "r1", "overseas", "2025A", "annual", "{}", "fingerprint"
        )
        store.save_candidate_scores([
            {
                "run_id": "r1",
                "code": "000002",
                "name": "缺失股份",
                "strategy": "overseas",
                "period": "2025A",
                "status": "data_missing",
                "data_missing_reason": "overseas_revenue_missing",
                "metrics_json": "{}",
            },
            {
                "run_id": "r1",
                "code": "600031",
                "name": "警告股份",
                "strategy": "overseas",
                "period": "2025A",
                "status": "watch",
                "watch_reason": "parse_warning",
                "metrics_json": json.dumps({
                    "overseas": {"parse_warning": "unit_ambiguous"}
                }),
            },
        ])

        review = build_quality_review(
            store, year=2025, period="2025A", pdf_dir=tmp_path
        )
        issues = review["issues"]

        assert review["summary"]["run_id"] == "r1"
        assert set(issues["issue_type"]) >= {
            "parse_warning",
            "low_confidence",
            "pdf_without_parsed_overseas",
            "screen_overseas_revenue_missing",
            "screen_parse_warning",
        }
        assert issues.iloc[0]["priority"] == "P1"
    finally:
        store.close()


def test_write_review_outputs_markdown_and_csv(tmp_path: Path):
    review = {
        "summary": {
            "year": 2025,
            "period": "2025A",
            "run_id": "r1",
            "pdf_count": 1,
            "parsed_count": 0,
            "issue_count": 1,
        },
        "issues": pd.DataFrame([
            {
                "priority": "P1",
                "issue_type": "pdf_without_parsed_overseas",
                "code": "000001",
                "name": "测试",
                "year": 2025,
                "confidence": None,
                "revenue_yi": None,
                "run_id": "r1",
                "pdf_path": "x.pdf",
                "evidence": "missing",
                "next_action": "rerun",
            }
        ]),
    }

    md_path = tmp_path / "review.md"
    csv_path = tmp_path / "review.csv"
    write_review(review, md_path=md_path, csv_path=csv_path)

    assert "策略三海外收入 Parser 质量池" in md_path.read_text(encoding="utf-8")
    assert "pdf_without_parsed_overseas" in csv_path.read_text(encoding="utf-8")


def test_empty_quality_review_still_writes_csv_header(tmp_path: Path):
    store = DuckDBStore(db_path=tmp_path / "t.duckdb")
    try:
        review = build_quality_review(
            store, year=2025, period="2025A", pdf_dir=tmp_path
        )
        md_path = tmp_path / "empty.md"
        csv_path = tmp_path / "empty.csv"
        write_review(review, md_path=md_path, csv_path=csv_path)

        assert "未发现 parser 质量问题" in md_path.read_text(encoding="utf-8")
        header = csv_path.read_text(encoding="utf-8-sig").splitlines()[0]
        assert "priority,issue_type,code" in header
    finally:
        store.close()


def test_verified_pure_domestic_skips_p3_issues(tmp_path: Path, monkeypatch):
    """Phase H：verified_pure_domestic.csv 中的 code 不再占 P3 issue 数。"""
    from scripts.review_overseas_parser_quality import _load_verified_pure_domestic

    # 用临时 CSV 覆盖 module-level 路径
    csv = tmp_path / "verified.csv"
    csv.write_text(
        "code,name,year,reason\n"
        "000001,测试甲,2025,纯内销核验通过\n"
        "000002,测试乙,2024,纯内销核验通过\n",
        encoding="utf-8",
    )
    import scripts.review_overseas_parser_quality as mod
    monkeypatch.setattr(mod, "VERIFIED_PURE_DOMESTIC_CSV", csv)

    # 2025 应含 000001 不含 000002
    verified_2025 = _load_verified_pure_domestic(2025)
    assert "000001" in verified_2025
    assert "000002" not in verified_2025
    # 2024 反之
    verified_2024 = _load_verified_pure_domestic(2024)
    assert "000002" in verified_2024
    assert "000001" not in verified_2024

    # 端到端：build_quality_review 跳过 verified code
    store = DuckDBStore(db_path=tmp_path / "t.duckdb")
    try:
        # 000001 有 PDF 但无 overseas_revenue 行 → 默认会进 P3 issue
        (tmp_path / "000001_2025_annual_report.pdf").touch()
        review = build_quality_review(
            store, year=2025, period="2025A", pdf_dir=tmp_path
        )
        # 000001 应被跳过，不出现在 issues
        codes_in_issues = set(review["issues"]["code"].astype(str).str.zfill(6))
        assert "000001" not in codes_in_issues
        assert review["summary"]["verified_pure_domestic_count"] == 1
    finally:
        store.close()


def test_build_quality_review_tolerates_empty_screen_metrics(tmp_path: Path):
    store = DuckDBStore(db_path=tmp_path / "t.duckdb")
    try:
        store.create_screen_run(
            "r1", "overseas", "2025A", "annual", "{}", "fingerprint"
        )
        store.save_candidate_scores([
            {
                "run_id": "r1",
                "code": "600031",
                "name": "测试股份",
                "strategy": "overseas",
                "period": "2025A",
                "status": "watch",
                "watch_reason": "parse_warning",
                "metrics_json": None,
            }
        ])

        review = build_quality_review(
            store, year=2025, period="2025A", pdf_dir=tmp_path
        )
        issue = review["issues"].query("issue_type == 'screen_parse_warning'").iloc[0]
        assert issue["evidence"] == "筛选侧 watch_reason=parse_warning"
    finally:
        store.close()
