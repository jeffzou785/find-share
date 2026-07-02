"""导入策略二A医药集采结构化事件 CSV。

CSV 推荐位置：data/exports/pharma_vbp_events.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.config import config
from src.storage import DuckDBStore
from src.utils.logging import configure_logging


logger = configure_logging(__name__)

REQUIRED_COLUMNS = {
    "code", "name", "product_name", "vbp_batch", "vbp_status",
    "source", "source_url", "evidence_text",
}
ALLOWED_STATUSES = {"won", "lost", "not_applicable", "unknown"}


def _clean_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def validate_vbp_events(df: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        errors.append(f"missing_columns:{','.join(sorted(missing))}")
    for col in sorted(REQUIRED_COLUMNS & set(df.columns)):
        blank = int((_clean_text(df[col]) == "").sum())
        if blank:
            errors.append(f"blank_{col}:{blank}")
    if "vbp_status" in df.columns:
        statuses = _clean_text(df["vbp_status"]).str.lower()
        bad = sorted(set(statuses[statuses != ""]) - ALLOWED_STATUSES)
        if bad:
            errors.append(f"invalid_vbp_status:{','.join(bad)}")
    for col in ("price_before", "price_after"):
        if col in df.columns:
            values = pd.to_numeric(df[col], errors="coerce")
            nonblank = _clean_text(df[col]) != ""
            bad_count = int((nonblank & values.isna()).sum())
            if bad_count:
                errors.append(f"invalid_{col}:{bad_count}")
    if {"price_before", "price_after"} <= set(df.columns):
        before = pd.to_numeric(df["price_before"], errors="coerce")
        after = pd.to_numeric(df["price_after"], errors="coerce")
        bad_price_order = int((before.notna() & after.notna() & (after > before)).sum())
        if bad_price_order:
            errors.append(f"price_after_gt_before:{bad_price_order}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--csv",
        default=str(config.EXPORTS_DIR / "pharma_vbp_events.csv"),
        help="集采事件 CSV 路径",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        logger.error(f"✗ CSV 不存在: {csv_path}")
        return 1

    df = pd.read_csv(csv_path, dtype={"code": str})
    if "code" in df.columns:
        df["code"] = _clean_text(df["code"]).str.zfill(6)
    if "vbp_status" in df.columns:
        df["vbp_status"] = _clean_text(df["vbp_status"]).str.lower()
    errors = validate_vbp_events(df)
    if errors:
        for err in errors:
            logger.error(f"✗ {err}")
        return 1

    with DuckDBStore() as store:
        n = store.save_pharma_vbp_events(df)
    logger.info(f"✓ 导入 pharma_vbp_events: {n} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
