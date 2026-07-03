"""P0 闭环辅助脚本测试。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        name,
        PROJECT_ROOT / "scripts" / f"{name}.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


validate_pharma_ground_truth = _load_script("validate_pharma_ground_truth")
import_pharma_vbp_events = _load_script("import_pharma_vbp_events")
init_pharma_templates = _load_script("init_pharma_templates")
export_labeling_queue = _load_script("export_labeling_queue")
p0_audit = _load_script("p0_audit")

from src.strategies.pharma_strategy import PHARMA_GROUND_TRUTH_COLUMNS
from src.storage import DuckDBStore


def test_validate_ground_truth_success():
    rows = []
    for i in range(30):
        rows.append({
            col: "x" for col in PHARMA_GROUND_TRUTH_COLUMNS
        })
        rows[-1]["code"] = str(i).zfill(6)
        rows[-1]["human_label"] = "watch"
        rows[-1]["label_reason"] = "manual baseline"
    df = pd.DataFrame(rows)
    assert validate_pharma_ground_truth.validate_ground_truth(df) == []


def test_validate_ground_truth_reports_missing_count_and_label():
    df = pd.DataFrame([
        {"code": "600276", "human_label": "bad_label", "label_reason": ""}
    ])
    errors = validate_pharma_ground_truth.validate_ground_truth(df)
    assert any(e.startswith("missing_columns:") for e in errors)
    assert "sample_count_below_30:1" in errors
    assert "invalid_human_label:bad_label" in errors


def test_validate_vbp_events_success_and_invalid_status():
    df = pd.DataFrame([
        {
            "code": "600276",
            "name": "恒瑞医药",
            "product_name": "药品A",
            "vbp_batch": "第八批",
            "vbp_status": "won",
            "source": "manual",
            "source_url": "https://example.com/source",
            "evidence_text": "中选",
        }
    ])
    assert import_pharma_vbp_events.validate_vbp_events(df) == []
    df.loc[0, "vbp_status"] = "maybe"
    assert "invalid_vbp_status:maybe" in import_pharma_vbp_events.validate_vbp_events(df)


def test_validate_vbp_events_rejects_blank_evidence():
    df = pd.DataFrame([
        {
            "code": "600276",
            "name": "恒瑞医药",
            "product_name": "药品A",
            "vbp_batch": "第八批",
            "vbp_status": "won",
            "source": "manual",
            "source_url": "",
            "evidence_text": "",
        }
    ])
    errors = import_pharma_vbp_events.validate_vbp_events(df)
    assert "blank_source_url:1" in errors
    assert "blank_evidence_text:1" in errors


def test_pharma_template_columns_cover_importers(tmp_path: Path):
    output_dir = tmp_path / "exports"
    assert init_pharma_templates._write_template(
        output_dir / "pharma_vbp_events.csv",
        init_pharma_templates.VBP_TEMPLATE_COLUMNS,
        force=False,
    ) is True
    assert (
        import_pharma_vbp_events.REQUIRED_COLUMNS
        <= set(init_pharma_templates.VBP_TEMPLATE_COLUMNS)
    )
    template = pd.read_csv(output_dir / "pharma_vbp_events.csv")
    assert list(template.columns) == init_pharma_templates.VBP_TEMPLATE_COLUMNS


def test_build_labeling_queue(tmp_path: Path):
    store = DuckDBStore(db_path=tmp_path / "t.duckdb")
    try:
        store.create_screen_run("r1", "overseas", "2025A", "annual", "{}", "f")
        store.save_candidate_scores([
            {"run_id": "r1", "code": "600276", "strategy": "overseas", "period": "2025A",
             "status": "hit", "hit_reason": "all_met"},
            {"run_id": "r1", "code": "000001", "strategy": "overseas", "period": "2025A",
             "status": "rejected", "reject_reason": "x"},
            {"run_id": "r1", "code": "600519", "strategy": "overseas", "period": "2025A",
             "status": "watch", "watch_reason": "near_threshold",
             "human_label": "watch"},
        ])
        queue = export_labeling_queue.build_labeling_queue(store)
        assert len(queue) == 1
        assert queue.iloc[0]["code"] == "600276"
    finally:
        store.close()


def test_p0_audit_requires_existing_research_pdfs_and_valid_ground_truth(tmp_path: Path):
    db_path = tmp_path / "audit.duckdb"
    research_dir = tmp_path / "research"
    annual_dir = tmp_path / "annual"
    exports_dir = tmp_path / "exports"
    research_dir.mkdir()
    annual_dir.mkdir()
    exports_dir.mkdir()

    store = DuckDBStore(db_path=db_path)
    try:
        reports = pd.DataFrame([
            {
                "code": "600276",
                "stock_name": "恒瑞医药",
                "title": f"研报{i}",
                "report_id": f"r{i}",
                "pdf_path": f"missing_{i}.pdf",
            }
            for i in range(200)
        ])
        store.save_broker_reports(reports)
    finally:
        store.close()

    gt_rows = []
    for i in range(30):
        row = {col: "x" for col in PHARMA_GROUND_TRUTH_COLUMNS}
        row["code"] = str(i).zfill(6)
        row["human_label"] = "bad_label"
        row["label_reason"] = "manual baseline"
        gt_rows.append(row)
    pd.DataFrame(gt_rows).to_csv(
        exports_dir / "pharma_vbp_ground_truth.csv",
        index=False,
    )

    audit = p0_audit.build_audit(
        db_path=db_path,
        research_dir=research_dir,
        annual_dir=annual_dir,
        exports_dir=exports_dir,
        docs_dir=tmp_path / "docs_missing",
    )
    assert audit["p0_status"]["research_report_metadata_200"] is True
    assert audit["p0_status"]["research_reports_200"] is False
    assert audit["broker_reports_pdf_existing"] == 0
    assert audit["p0_status"]["pharma_ground_truth_30"] is False
    assert audit["pharma_ground_truth_validation_errors"] == [
        "invalid_human_label:bad_label"
    ]
    assert audit["p0_status"]["pharma_ground_truth_rulebook"] is False
    assert audit["p0_status"]["global_stock_mapping_nonempty"] is False


def test_p0_audit_labels_scope_defaults_to_latest_successful_run(tmp_path: Path):
    db_path = tmp_path / "audit.duckdb"
    store = DuckDBStore(db_path=db_path)
    try:
        store.create_screen_run("old", "overseas", "2025A", "annual", "{}", "f1")
        store.finish_screen_run("old", "success", counts={"hit": 1})
        store.conn.execute(
            "UPDATE screen_runs SET started_at = CURRENT_TIMESTAMP - INTERVAL '1 HOUR' "
            "WHERE run_id = 'old'"
        )
        store.save_candidate_scores([
            {"run_id": "old", "code": "600276", "strategy": "overseas",
             "period": "2025A", "status": "hit", "hit_reason": "old"},
        ])

        store.create_screen_run("new", "overseas", "2025A", "annual", "{}", "f2")
        store.finish_screen_run("new", "success", counts={"hit": 1})
        store.save_candidate_scores([
            {"run_id": "new", "code": "600519", "strategy": "overseas",
             "period": "2025A", "status": "hit", "hit_reason": "new",
             "human_label": "hit", "label_reason": "manual"},
        ])
    finally:
        store.close()

    latest = p0_audit.build_audit(db_path=db_path)
    all_runs = p0_audit.build_audit(db_path=db_path, latest_only=False)
    assert latest["label_scope"]["run_ids"] == ["new"]
    assert latest["hit_watch_unlabeled"] == 0
    assert latest["p0_status"]["candidate_labels_done"] is True
    assert all_runs["hit_watch_unlabeled"] == 1
    assert all_runs["p0_status"]["candidate_labels_done"] is False
