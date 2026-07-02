"""校验策略二 ground truth CSV 是否达到 P0 最小要求。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.config import config
from src.strategies.pharma_strategy import PHARMA_GROUND_TRUTH_COLUMNS
from src.utils.logging import configure_logging


logger = configure_logging(__name__)
ALLOWED_LABELS = {"hit", "watch", "false_positive", "false_negative"}
MIN_SAMPLES = 30


def _clean_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def validate_ground_truth(df: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    missing = set(PHARMA_GROUND_TRUTH_COLUMNS) - set(df.columns)
    if missing:
        errors.append(f"missing_columns:{','.join(sorted(missing))}")
    if len(df) < MIN_SAMPLES:
        errors.append(f"sample_count_below_{MIN_SAMPLES}:{len(df)}")
    for col in ("code", "name", "human_label", "label_reason"):
        if col in df.columns:
            blank = int((_clean_text(df[col]) == "").sum())
            if blank:
                errors.append(f"blank_{col}:{blank}")
    if "human_label" in df.columns:
        labels = set(_clean_text(df["human_label"]))
        labels.discard("")
        bad = sorted(labels - ALLOWED_LABELS)
        if bad:
            errors.append(f"invalid_human_label:{','.join(bad)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--csv",
        default=str(config.EXPORTS_DIR / "pharma_vbp_ground_truth.csv"),
        help="ground truth CSV 路径",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        logger.error(f"✗ CSV 不存在: {csv_path}")
        return 1
    df = pd.read_csv(csv_path, dtype={"code": str})
    errors = validate_ground_truth(df)
    if errors:
        for err in errors:
            logger.error(f"✗ {err}")
        return 1
    logger.info(f"✓ ground truth 校验通过: {len(df)} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
