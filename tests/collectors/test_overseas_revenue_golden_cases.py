"""Golden case registry for difficult overseas revenue PDFs.

The default tests keep this lightweight and only validate the registry shape.
Set RUN_PDF_GOLDEN=1 to run the slow local-PDF parser checks.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

from src.collectors.annual_report_parser import parse_annual_report, select_best_record


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_CASES = PROJECT_ROOT / "tests/fixtures/overseas_revenue_golden_cases.csv"
REQUIRED_COLUMNS = {
    "code",
    "name",
    "year",
    "pdf_path",
    "expected_status",
    "expected_best_yi_min",
    "expected_best_yi_max",
    "expected_preferred_yi_min",
    "expected_preferred_yi_max",
    "expected_error_contains",
    "next_action",
}
EXPECTED_STATUSES = {"fixed", "candidate_fallback_required", "parse_fail"}


def _load_cases() -> list[dict[str, str]]:
    with GOLDEN_CASES.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _maybe_float(value: str) -> float | None:
    text = (value or "").strip()
    return float(text) if text else None


def test_golden_cases_registry_shape():
    cases = _load_cases()
    assert cases
    assert set(cases[0].keys()) == REQUIRED_COLUMNS
    assert {c["code"] for c in cases} == {
        "001288", "002145", "001311", "002085", "601766",
        "600066", "000913", "605333", "688633", "000030",
        "600523", "603086", "603969", "300230",
    }

    for case in cases:
        assert len(case["code"]) == 6
        assert case["year"] in {"2024", "2025"}
        assert case["expected_status"] in EXPECTED_STATUSES
        assert case["pdf_path"].startswith("data/pdfs/annual_reports/")
        assert case["next_action"]


@pytest.mark.skipif(
    os.environ.get("RUN_PDF_GOLDEN") != "1",
    reason="Set RUN_PDF_GOLDEN=1 to run slow local-PDF golden checks.",
)
def test_local_pdf_golden_cases_when_enabled():
    for case in _load_cases():
        pdf_path = PROJECT_ROOT / case["pdf_path"]
        assert pdf_path.exists(), f"missing local PDF: {pdf_path}"

        result = parse_annual_report(pdf_path, stock_code=case["code"])
        if case["expected_status"] == "parse_fail":
            assert not result.success
            assert case["expected_error_contains"] in result.error
            continue

        assert result.success
        best, _warnings = select_best_record(result.records)
        assert best is not None
        best_yi = (best.revenue_yuan or 0.0) / 1e8
        best_min = _maybe_float(case["expected_best_yi_min"])
        best_max = _maybe_float(case["expected_best_yi_max"])
        if best_min is not None and best_max is not None:
            assert best_min <= best_yi <= best_max
