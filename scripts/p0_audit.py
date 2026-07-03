"""生成 P0 闭环状态审计报告。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.config import config
from src.storage import DuckDBStore
from src.utils.logging import configure_logging
from scripts.import_pharma_vbp_events import validate_vbp_events
from scripts.validate_pharma_ground_truth import MIN_SAMPLES, validate_ground_truth


logger = configure_logging(__name__)
RESEARCH_REPORTS_MIN = 200


def _count_pdf_files(path: Path) -> int:
    return sum(1 for _ in path.rglob("*.pdf")) + sum(1 for _ in path.rglob("*.PDF"))


def _pdf_path_exists(raw_path: object, research_dir: Path) -> bool:
    if raw_path is None or pd.isna(raw_path):
        return False
    text = str(raw_path).strip()
    if not text:
        return False
    path = Path(text)
    candidates = [path] if path.is_absolute() else [
        PROJECT_ROOT / path,
        research_dir / path,
    ]
    return any(p.exists() and p.is_file() for p in candidates)


def _latest_run_ids(
    store: DuckDBStore,
    *,
    period: str | None,
    strategy: str | None,
    latest_only: bool,
) -> list[str]:
    sql = (
        "SELECT run_id FROM screen_runs "
        "WHERE status IN ('success', 'partial_success')"
    )
    params: list[object] = []
    if period:
        sql += " AND period = ?"
        params.append(period)
    if strategy:
        sql += " AND strategy = ?"
        params.append(strategy)
    sql += " ORDER BY started_at DESC"
    if latest_only:
        sql += " LIMIT 1"
    return [row[0] for row in store.conn.execute(sql, params).fetchall()]


def _count_labels(
    store: DuckDBStore,
    *,
    run_ids: list[str],
    strategy: str | None,
) -> dict[str, int]:
    if not run_ids:
        return {
            "label_scope_candidate_scores": 0,
            "candidate_labels": 0,
            "hit_watch_unlabeled": 0,
        }

    placeholders = ", ".join("?" for _ in run_ids)
    base = f"FROM candidate_scores WHERE run_id IN ({placeholders})"
    params: list[object] = list(run_ids)
    if strategy and strategy != "all":
        base += " AND strategy = ?"
        params.append(strategy)

    return {
        "label_scope_candidate_scores": int(store.conn.execute(
            f"SELECT COUNT(*) {base}", params,
        ).fetchone()[0]),
        "candidate_labels": int(store.conn.execute(
            f"SELECT COUNT(*) {base} "
            "AND human_label IS NOT NULL AND TRIM(human_label) != ''",
            params,
        ).fetchone()[0]),
        "hit_watch_unlabeled": int(store.conn.execute(
            f"SELECT COUNT(*) {base} "
            "AND status IN ('hit','watch') "
            "AND (human_label IS NULL OR TRIM(human_label) = '')",
            params,
        ).fetchone()[0]),
    }


def build_audit(
    *,
    period: str | None = None,
    strategy: str | None = None,
    latest_only: bool = True,
    db_path: Path | None = None,
    research_dir: Path | None = None,
    annual_dir: Path | None = None,
    exports_dir: Path | None = None,
    docs_dir: Path | None = None,
) -> dict:
    research_dir = research_dir or config.RESEARCH_REPORT_DIR
    annual_dir = annual_dir or config.ANNUAL_REPORT_PDF_DIR
    exports_dir = exports_dir or config.EXPORTS_DIR
    docs_dir = docs_dir or (PROJECT_ROOT / "docs")
    audit: dict = {
        "label_scope": {
            "period": period,
            "strategy": strategy,
            "latest_only": latest_only,
        },
        "research_pdf_files": _count_pdf_files(research_dir),
        "annual_report_pdf_files": _count_pdf_files(annual_dir),
        "pharma_ground_truth_rulebook_exists": (
            docs_dir / "pharma_ground_truth_rulebook.md"
        ).exists(),
    }
    with DuckDBStore(db_path=db_path) as store:
        audit["broker_reports"] = int(store.conn.execute("SELECT COUNT(*) FROM broker_reports").fetchone()[0])
        audit["broker_reports_with_pdf"] = int(store.conn.execute(
            "SELECT COUNT(*) FROM broker_reports WHERE pdf_path IS NOT NULL AND pdf_path != ''"
        ).fetchone()[0])
        pdf_paths = store.conn.execute(
            "SELECT pdf_path FROM broker_reports WHERE pdf_path IS NOT NULL AND TRIM(pdf_path) != ''"
        ).df()
        audit["broker_reports_pdf_existing"] = int(
            sum(_pdf_path_exists(p, research_dir) for p in pdf_paths.get("pdf_path", []))
        )
        audit["screen_runs"] = int(store.conn.execute("SELECT COUNT(*) FROM screen_runs").fetchone()[0])
        audit["candidate_scores"] = int(store.conn.execute("SELECT COUNT(*) FROM candidate_scores").fetchone()[0])
        run_ids = _latest_run_ids(
            store, period=period, strategy=strategy, latest_only=latest_only,
        )
        audit["label_scope"]["run_ids"] = run_ids
        audit.update(_count_labels(store, run_ids=run_ids, strategy=strategy))
        vbp = store.conn.execute("SELECT * FROM pharma_vbp_events").df()
        vbp_errors = validate_vbp_events(vbp) if not vbp.empty else []
        audit["pharma_vbp_events"] = int(len(vbp))
        audit["pharma_vbp_validation_errors"] = vbp_errors
        audit["pharma_vbp_valid_events"] = int(len(vbp) if not vbp_errors else 0)

    gt_path = exports_dir / "pharma_vbp_ground_truth.csv"
    if gt_path.exists():
        gt = pd.read_csv(gt_path, dtype={"code": str})
        gt_errors = validate_ground_truth(gt)
        audit["pharma_ground_truth_rows"] = int(len(gt))
        audit["pharma_ground_truth_labeled"] = int(
            (gt.get("human_label", pd.Series(dtype=str)).fillna("").astype(str).str.strip() != "").sum()
        )
        audit["pharma_ground_truth_validation_errors"] = gt_errors
        audit["pharma_ground_truth_valid"] = not gt_errors
    else:
        audit["pharma_ground_truth_rows"] = 0
        audit["pharma_ground_truth_labeled"] = 0
        audit["pharma_ground_truth_validation_errors"] = ["csv_missing"]
        audit["pharma_ground_truth_valid"] = False

    audit["p0_targets"] = {
        "broker_reports_min": RESEARCH_REPORTS_MIN,
        "broker_report_pdfs_min": RESEARCH_REPORTS_MIN,
        "current_hit_watch_labels_required": audit["hit_watch_unlabeled"] == 0,
        "pharma_ground_truth_min": MIN_SAMPLES,
        "pharma_structured_source_required": audit["pharma_vbp_valid_events"] > 0,
        "pharma_ground_truth_rulebook_required": True,
    }
    audit["p0_status"] = {
        "research_report_metadata_200": audit["broker_reports"] >= RESEARCH_REPORTS_MIN,
        "research_reports_200": audit["broker_reports_pdf_existing"] >= RESEARCH_REPORTS_MIN,
        "candidate_labels_done": audit["hit_watch_unlabeled"] == 0,
        "pharma_ground_truth_30": (
            audit["pharma_ground_truth_labeled"] >= MIN_SAMPLES
            and audit["pharma_ground_truth_valid"]
        ),
        "pharma_structured_source_nonempty": audit["pharma_vbp_valid_events"] > 0,
        "pharma_ground_truth_rulebook": audit["pharma_ground_truth_rulebook_exists"],
    }
    return audit


def write_report(audit: dict, exports_dir: Path | None = None) -> Path:
    exports_dir = exports_dir or config.EXPORTS_DIR
    out_json = exports_dir / "p0_audit.json"
    out_md = exports_dir / "p0_audit.md"
    exports_dir.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# P0 Audit",
        "",
        f"- label_scope: {audit['label_scope']}",
        f"- broker_reports: {audit['broker_reports']} / {RESEARCH_REPORTS_MIN}",
        f"- broker_reports_with_pdf: {audit['broker_reports_with_pdf']}",
        f"- broker_reports_pdf_existing: {audit['broker_reports_pdf_existing']} / {RESEARCH_REPORTS_MIN}",
        f"- research_pdf_files: {audit['research_pdf_files']}",
        f"- candidate_scores: {audit['candidate_scores']}",
        f"- label_scope_candidate_scores: {audit['label_scope_candidate_scores']}",
        f"- candidate_labels: {audit['candidate_labels']}",
        f"- hit_watch_unlabeled: {audit['hit_watch_unlabeled']}",
        f"- pharma_ground_truth_labeled: {audit['pharma_ground_truth_labeled']} / {MIN_SAMPLES}",
        f"- pharma_ground_truth_validation_errors: {audit['pharma_ground_truth_validation_errors']}",
        f"- pharma_ground_truth_rulebook_exists: {audit['pharma_ground_truth_rulebook_exists']}",
        f"- pharma_vbp_events: {audit['pharma_vbp_events']}",
        f"- pharma_vbp_valid_events: {audit['pharma_vbp_valid_events']}",
        f"- pharma_vbp_validation_errors: {audit['pharma_vbp_validation_errors']}",
        "",
        "## Status",
        "",
    ]
    for key, value in audit["p0_status"].items():
        mark = "OK" if value else "TODO"
        lines.append(f"- {mark}: {key}")
    out_md.write_text("\n".join(lines), encoding="utf-8")
    return out_md


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--period", default=None, help="只审计指定报告期的最新 run")
    parser.add_argument("--strategy", default=None, help="只审计指定策略/all 的最新 run")
    parser.add_argument(
        "--all-runs", action="store_true",
        help="标注审计覆盖匹配条件下所有成功/部分成功 run，而不是最新 run",
    )
    args = parser.parse_args()

    audit = build_audit(
        period=args.period,
        strategy=args.strategy,
        latest_only=not args.all_runs,
    )
    out = write_report(audit)
    logger.info(f"✓ P0 审计报告: {out}")
    return 0 if all(audit["p0_status"].values()) else 2


if __name__ == "__main__":
    sys.exit(main())
