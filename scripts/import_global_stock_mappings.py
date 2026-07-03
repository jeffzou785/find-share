"""导入 A/H/港股代码映射。

用于策略二B港股扩展池和 A+H 对照：
- hk_code 标准化为 5 位港股代码
- yahoo_symbol 供 Yahoo chart/quoteSummary 使用
- eastmoney_secucode/eastmoney_secid 供东财港股接口使用
- hk_disclosure_source_gap 默认 True，提醒港交所公告全文尚未接入
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.collectors.global_stock_mapping import (
    GLOBAL_STOCK_MAPPING_COLUMNS,
    build_hk_mapping_frame,
)
from src.config import config
from src.storage import DuckDBStore
from src.utils.logging import configure_logging


logger = configure_logging(__name__)

TEMPLATE_COLUMNS = [
    "a_code",
    "hk_code",
    "name",
    "source",
    "hk_disclosure_source_gap",
]
REQUIRED_COLUMNS = {"hk_code"}


def write_template(path: Path, *, force: bool = False) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=TEMPLATE_COLUMNS).to_csv(path, index=False)
    return True


def validate_mapping_rows(df: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        errors.append(f"missing_columns:{','.join(sorted(missing))}")
        return errors
    if df.empty:
        errors.append("empty_csv")
        return errors

    try:
        normalized = build_hk_mapping_frame(df)
    except Exception as e:
        errors.append(f"invalid_mapping:{type(e).__name__}:{e}")
        return errors

    blanks = normalized["hk_code"].fillna("").astype(str).str.strip() == ""
    if blanks.any():
        errors.append(f"blank_hk_code:{int(blanks.sum())}")
    duplicated = normalized["hk_code"].duplicated(keep=False)
    if duplicated.any():
        values = sorted(set(normalized.loc[duplicated, "hk_code"].tolist()))
        errors.append(f"duplicate_hk_code:{','.join(values)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--csv", type=Path, default=None, help="待导入映射 CSV")
    parser.add_argument(
        "--init-template",
        action="store_true",
        help="初始化 data/exports/global_stock_mappings.csv 模板",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=config.EXPORTS_DIR / "global_stock_mappings.csv",
        help="模板输出路径",
    )
    parser.add_argument("--force", action="store_true", help="覆盖已有模板")
    parser.add_argument("--dry-run", action="store_true", help="只校验不入库")
    args = parser.parse_args()

    if args.init_template:
        changed = write_template(args.output, force=args.force)
        verb = "写入" if changed else "已存在，跳过"
        logger.info(f"✓ {verb}: {args.output}")
        return 0

    if args.csv is None:
        parser.error("需要 --csv 或 --init-template")
    raw = pd.read_csv(args.csv, dtype=str).fillna("")
    errors = validate_mapping_rows(raw)
    if errors:
        for error in errors:
            logger.error(f"✗ {error}")
        return 2

    df = build_hk_mapping_frame(raw)
    if args.dry_run:
        logger.info(f"✓ dry-run ok: {len(df)} rows")
        return 0

    with DuckDBStore() as store:
        inserted = store.save_global_stock_mappings(df)
    logger.info(
        f"✓ 导入 A/H/港股映射 {inserted} 条，字段: {GLOBAL_STOCK_MAPPING_COLUMNS}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
