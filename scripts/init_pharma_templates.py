"""初始化策略二数据闭环 CSV 模板。

默认写入：
- data/exports/pharma_vbp_events.csv
- data/exports/pharma_vbp_ground_truth.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from scripts.import_pharma_vbp_events import REQUIRED_COLUMNS
from src.config import config
from src.strategies.pharma_strategy import PHARMA_GROUND_TRUTH_COLUMNS
from src.utils.logging import configure_logging


logger = configure_logging(__name__)

VBP_TEMPLATE_COLUMNS = [
    "code", "name", "product_name", "vbp_batch", "vbp_status",
    "tender_date", "province", "price_before", "price_after",
    "volume_commitment", "source", "source_url", "evidence_text",
]


def _write_template(path: Path, columns: list[str], *, force: bool) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=columns).to_csv(path, index=False)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--force", action="store_true", help="覆盖已有模板")
    parser.add_argument("--output-dir", type=Path, default=config.EXPORTS_DIR)
    args = parser.parse_args()

    # 编程期防漂移：模板必须覆盖导入器要求的必填列。
    missing = REQUIRED_COLUMNS - set(VBP_TEMPLATE_COLUMNS)
    if missing:
        logger.error(f"✗ VBP 模板缺少必填列: {sorted(missing)}")
        return 1

    targets = [
        (
            args.output_dir / "pharma_vbp_events.csv",
            VBP_TEMPLATE_COLUMNS,
        ),
        (
            args.output_dir / "pharma_vbp_ground_truth.csv",
            PHARMA_GROUND_TRUTH_COLUMNS,
        ),
    ]
    for path, columns in targets:
        changed = _write_template(path, list(columns), force=args.force)
        verb = "写入" if changed else "已存在，跳过"
        logger.info(f"✓ {verb}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
