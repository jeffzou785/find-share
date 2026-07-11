"""策略二A ground truth 复盘脚本测试。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "review_pharma_ground_truth",
        PROJECT_ROOT / "scripts" / "review_pharma_ground_truth.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


review_pharma_ground_truth = _load_script()


def test_classify_relation_positive_and_negative_cases():
    assert (
        review_pharma_ground_truth.classify_relation("watch", "hit")
        == "aligned_positive"
    )
    assert (
        review_pharma_ground_truth.classify_relation("hit", "rejected")
        == "positive_label_missed"
    )
    assert (
        review_pharma_ground_truth.classify_relation("false_positive", "watch")
        == "false_positive_promoted"
    )
    assert (
        review_pharma_ground_truth.classify_relation("false_positive", "rejected")
        == "aligned_negative"
    )


def test_build_review_joins_ground_truth_and_scores():
    gt = pd.DataFrame([
        {
            "code": "600276",
            "name": "恒瑞医药",
            "sub_industry": "化学制剂",
            "vbp_status": "won",
            "human_label": "watch",
            "label_reason": "manual",
        },
        {
            "code": "300760",
            "name": "迈瑞医疗",
            "sub_industry": "医疗器械",
            "vbp_status": "not_applicable",
            "human_label": "false_positive",
            "label_reason": "manual",
        },
    ])
    scores = pd.DataFrame([
        {
            "code": "600276",
            "status": "hit",
            "hit_reason": "vbp_recovery_confirmed",
            "metrics_json": (
                '{"source_status":{"extra":{"vbp_status":"won",'
                '"matched_keyword":"化学制剂"}}}'
            ),
        },
        {
            "code": "300760",
            "status": "watch",
            "watch_reason": "partial_vbp_recovery",
            "metrics_json": "{}",
        },
    ])

    review = review_pharma_ground_truth.build_review(gt, scores)

    by_code = {row["code"]: row for _, row in review.iterrows()}
    assert by_code["600276"]["relation"] == "aligned_positive"
    assert by_code["600276"]["matched_keyword"] == "化学制剂"
    assert by_code["300760"]["relation"] == "false_positive_promoted"


def test_write_review_creates_markdown_and_csv(tmp_path: Path):
    review = pd.DataFrame([
        {
            "code": "600276",
            "name": "恒瑞医药",
            "human_label": "hit",
            "screen_status": "rejected",
            "relation": "positive_label_missed",
            "screen_reason": "vbp_recovery_not_confirmed",
        }
    ])
    out = review_pharma_ground_truth.write_review(
        review,
        run_id="r1",
        output=tmp_path / "review.md",
    )

    text = out.read_text(encoding="utf-8")
    assert "positive_label_missed" in text
    assert "600276" in text
    assert (tmp_path / "review.csv").exists()
