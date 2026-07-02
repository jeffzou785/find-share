"""导出待人工标注的候选清单。"""
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


def build_labeling_queue(store: DuckDBStore, limit: int | None = None) -> pd.DataFrame:
    sql = (
        "SELECT cs.run_id, cs.code, cs.name, cs.strategy, cs.period, cs.status, "
        "cs.hit_reason, cs.watch_reason, cs.reject_reason, cs.data_missing_reason, "
        "cs.human_label, cs.label_reason, sr.started_at "
        "FROM candidate_scores cs "
        "LEFT JOIN screen_runs sr ON cs.run_id = sr.run_id "
        "WHERE cs.status IN ('hit', 'watch') "
        "AND (cs.human_label IS NULL OR cs.human_label = '') "
        "ORDER BY sr.started_at DESC NULLS LAST, cs.strategy, cs.status, cs.code"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return store.conn.execute(sql).df()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--output",
        default=str(config.EXPORTS_DIR / "human_label_queue.csv"),
        help="输出 CSV 路径",
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    with DuckDBStore() as store:
        df = build_labeling_queue(store, limit=args.limit)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    logger.info(f"✓ 待标注清单: {len(df)} 行 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
