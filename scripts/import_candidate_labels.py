"""从人工标注 CSV 回写 candidate_scores。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.storage import DuckDBStore
from src.utils.logging import configure_logging


logger = configure_logging(__name__)
ALLOWED_LABELS = {"hit", "watch", "false_positive", "false_negative"}
REQUIRED_COLUMNS = {"run_id", "code", "strategy", "human_label"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv", help="人工标注 CSV，需包含 run_id/code/strategy/human_label")
    args = parser.parse_args()

    path = Path(args.csv)
    if not path.exists():
        logger.error(f"✗ CSV 不存在: {path}")
        return 1
    df = pd.read_csv(path, dtype={"code": str})
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        logger.error(f"✗ missing_columns:{','.join(sorted(missing))}")
        return 1
    if "label_reason" not in df.columns:
        df["label_reason"] = None
    df = df[df["human_label"].notna() & (df["human_label"].astype(str).str.strip() != "")]
    bad = sorted(set(df["human_label"].astype(str)) - ALLOWED_LABELS)
    if bad:
        logger.error(f"✗ invalid_human_label:{','.join(bad)}")
        return 1

    updated = 0
    missing_rows = 0
    with DuckDBStore() as store:
        for _, row in df.iterrows():
            ok = store.update_candidate_label(
                run_id=str(row["run_id"]),
                code=str(row["code"]).zfill(6),
                strategy=str(row["strategy"]),
                human_label=str(row["human_label"]),
                label_reason=(
                    None if pd.isna(row.get("label_reason"))
                    else str(row.get("label_reason"))
                ),
            )
            updated += int(ok)
            missing_rows += int(not ok)
    logger.info(f"✓ 标签回写: updated={updated} missing={missing_rows}")
    return 0 if missing_rows == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
