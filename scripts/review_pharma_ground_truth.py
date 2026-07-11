"""复盘策略二A ground truth 与 pharma-screen 结果的一致性。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import config
from src.storage import DuckDBStore
from src.strategies.pharma_strategy import PHARMA_VBP_STRATEGY
from src.utils.logging import configure_logging


logger = configure_logging(__name__)
POSITIVE_LABELS = {"hit", "watch", "false_negative"}
NEGATIVE_LABELS = {"false_positive"}
POSITIVE_STATUSES = {"hit", "watch"}


def _latest_pharma_run_id(store: DuckDBStore, period: str | None = None) -> str | None:
    sql = (
        "SELECT run_id FROM screen_runs "
        "WHERE strategy = ? AND status IN ('success','partial_success')"
    )
    params: list[object] = [PHARMA_VBP_STRATEGY]
    if period:
        sql += " AND period = ?"
        params.append(period)
    sql += " ORDER BY started_at DESC LIMIT 1"
    row = store.conn.execute(sql, params).fetchone()
    return str(row[0]) if row else None


def _reason(row: pd.Series) -> str:
    for col in ("hit_reason", "watch_reason", "reject_reason", "data_missing_reason", "error"):
        value = row.get(col)
        if value is not None and not pd.isna(value) and str(value).strip():
            return str(value)
    return ""


def _metrics_extra(raw: object) -> dict:
    if raw is None or pd.isna(raw) or not str(raw).strip():
        return {}
    try:
        return (json.loads(str(raw)).get("source_status") or {}).get("extra") or {}
    except json.JSONDecodeError:
        return {}


def _check_summary(extra: dict) -> str:
    raw = extra.get("recovery_checks_json")
    if not raw:
        return ""
    try:
        checks = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    parts = []
    for key, obj in checks.items():
        passed = obj.get("passed")
        mark = "Y" if passed is True else ("N" if passed is False else "?")
        parts.append(f"{key}={mark}")
    return ";".join(parts)


def classify_relation(human_label: str, status: str) -> str:
    label = str(human_label or "").strip()
    status = str(status or "").strip()
    if label in POSITIVE_LABELS:
        if status in POSITIVE_STATUSES:
            return "aligned_positive"
        return "positive_label_missed"
    if label in NEGATIVE_LABELS:
        if status in POSITIVE_STATUSES:
            return "false_positive_promoted"
        return "aligned_negative"
    return "unlabeled_or_unknown"


def build_review(ground_truth: pd.DataFrame, scores: pd.DataFrame) -> pd.DataFrame:
    gt = ground_truth.copy()
    sc = scores.copy()
    gt["code"] = gt["code"].astype(str).str.zfill(6)
    sc["code"] = sc["code"].astype(str).str.zfill(6)
    keep_cols = [
        "code", "status", "hit_reason", "watch_reason", "reject_reason",
        "data_missing_reason", "error", "metrics_json",
    ]
    merged = gt.merge(sc[[c for c in keep_cols if c in sc.columns]], on="code", how="left")
    rows = []
    for _, row in merged.iterrows():
        extra = _metrics_extra(row.get("metrics_json"))
        status = "" if pd.isna(row.get("status")) else str(row.get("status"))
        rows.append({
            "code": row.get("code"),
            "name": row.get("name"),
            "human_label": row.get("human_label"),
            "screen_status": status or "not_in_run",
            "relation": classify_relation(str(row.get("human_label", "")), status),
            "screen_reason": _reason(row),
            "sub_industry": row.get("sub_industry"),
            "vbp_status_gt": row.get("vbp_status"),
            "vbp_status_screen": extra.get("vbp_status", ""),
            "matched_keyword": extra.get("matched_keyword", ""),
            "checks": _check_summary(extra),
            "label_reason": row.get("label_reason"),
        })
    return pd.DataFrame(rows)


def write_review(review: pd.DataFrame, *, run_id: str, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output.with_suffix(".csv")
    review.to_csv(csv_path, index=False, encoding="utf-8-sig")
    relation_counts = review["relation"].value_counts(dropna=False).to_dict()
    status_counts = review["screen_status"].value_counts(dropna=False).to_dict()
    lines = [
        "# Pharma Ground Truth Review",
        "",
        f"- run_id: `{run_id}`",
        f"- samples: {len(review)}",
        "",
        "## Relation Counts",
        "",
    ]
    for key, value in relation_counts.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Screen Status Counts", ""])
    for key, value in status_counts.items():
        lines.append(f"- {key}: {value}")

    focus = review[review["relation"].isin(["positive_label_missed", "false_positive_promoted"])]
    lines.extend(["", "## Mismatches", ""])
    if focus.empty:
        lines.append("- none")
    else:
        lines.append("| code | name | label | status | reason |")
        lines.append("|---|---|---|---|---|")
        for _, row in focus.iterrows():
            lines.append(
                f"| {row['code']} | {row['name']} | {row['human_label']} | "
                f"{row['screen_status']} | {row['screen_reason']} |"
            )
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run-id", default=None, help="指定 pharma-screen run_id")
    parser.add_argument("--period", default=None, help="未指定 run-id 时取该 period 最新 pharma run")
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=config.EXPORTS_DIR / "pharma_vbp_ground_truth.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=config.EXPORTS_DIR / "pharma_ground_truth_review.md",
    )
    args = parser.parse_args()

    if not args.ground_truth.exists():
        logger.error(f"✗ ground truth 不存在: {args.ground_truth}")
        return 1
    gt = pd.read_csv(args.ground_truth, dtype={"code": str})
    with DuckDBStore() as store:
        run_id = args.run_id or _latest_pharma_run_id(store, args.period)
        if not run_id:
            logger.error("✗ 未找到 pharma_vbp screen run")
            return 1
        scores = store.load_candidate_scores(run_id)
    review = build_review(gt, scores)
    out = write_review(review, run_id=run_id, output=args.output)
    logger.info(f"✓ ground truth 复盘: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
