"""P2-3: 研报/事件 claim 入库（evidence_claims 表）。

阶段策略：先建 schema + 1-2 个手填样本，跑通端到端。研报 PDF 入 RAG 后
可批量补全（按 evidence_type 关键词抽取：overseas_order / capacity / customer /
license_out / fda_cde）。

输入：CSV 文件，列见 tests/fixtures/evidence_claims_seed.csv
输出：写入 evidence_claims 表
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.storage import DuckDBStore  # noqa: E402

DEFAULT_CSV = PROJECT_ROOT / "tests/fixtures/evidence_claims_seed.csv"

VALID_EVIDENCE_TYPES = {
    "overseas_order",   # 海外订单
    "capacity",         # 产能
    "customer",         # 客户
    "license_out",      # License-out / BD 交易
    "fda_cde",          # FDA / CDE 进展
    "vbp_event",        # 集采事件（兼容 pharma_vbp_events 抽取）
    "guidance",         # 业绩指引（管理层 forward-looking）
}
VALID_CONFIDENCE = {"high", "medium", "low"}


def _validate_row(row: dict) -> list[str]:
    errs = []
    if not str(row.get("code", "")).strip():
        errs.append("code 必填")
    if not str(row.get("claim_text", "")).strip():
        errs.append("claim_text 必填")
    if not str(row.get("report_date", "")).strip():
        # report_date 必填：避免同 code+claim 在 DuckDB NULL ≠ NULL 时无限堆积
        errs.append("report_date 必填")
    if not str(row.get("evidence_type", "")).strip():
        errs.append("evidence_type 必填")
    elif row["evidence_type"] not in VALID_EVIDENCE_TYPES:
        errs.append(f"evidence_type 不合法：{row['evidence_type']}")
    if row.get("confidence") and row["confidence"] not in VALID_CONFIDENCE:
        errs.append(f"confidence 不合法：{row['confidence']}")
    return errs


def load_csv(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    errors: list[str] = []
    for i, row in df.iterrows():
        row_errs = _validate_row(row.to_dict())
        for e in row_errs:
            errors.append(f"row {i} ({row.get('code', '?')}): {e}")
    if errors:
        print(f"✗ CSV 校验失败 ({len(errors)} 条)：")
        for e in errors[:10]:
            print(f"  {e}")
        raise ValueError(f"{len(errors)} validation errors")
    # 归一化 code 为 6 位
    df["code"] = df["code"].astype(str).str.zfill(6)
    # report_date 转日期
    if "report_date" in df.columns:
        df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce").dt.date
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv", default=str(DEFAULT_CSV),
        help=f"CSV 路径，默认 {DEFAULT_CSV}",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"✗ CSV 不存在：{csv_path}")
        return 1

    df = load_csv(csv_path)
    print(f"  读取 {len(df)} 条 claim")

    store = DuckDBStore()
    try:
        n = store.save_evidence_claims(df)
        print(f"✓ 写入 evidence_claims：{n} 条")
        # 验证
        rows = store.conn.execute(
            "SELECT evidence_type, COUNT(*) FROM evidence_claims "
            "GROUP BY evidence_type ORDER BY COUNT(*) DESC"
        ).fetchall()
        for r in rows:
            print(f"  {r[0]}: {r[1]}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
