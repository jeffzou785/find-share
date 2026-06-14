"""批量解析已下载的年报 PDF 并入库 overseas_revenue 表。

工作流：
1. 扫描 data/pdfs/annual_reports/ 下所有 PDF
2. 用 annual_report_parser 解析
3. 同一股票多条记录，取金额最大的作为"境外收入"代表
4. 入库 DuckDB overseas_revenue 表
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.collectors.annual_report_parser import parse_annual_report
from src.config import config
from src.storage import DuckDBStore


def main(year: int = 2024) -> int:
    pdf_dir = config.ANNUAL_REPORT_PDF_DIR
    print(f"扫描目录: {pdf_dir}")
    pdfs = sorted(pdf_dir.glob(f"*_{year}_annual_report.pdf"))
    print(f"找到 {len(pdfs)} 份年报 PDF\n")

    if not pdfs:
        print("✗ 没有找到 PDF。先跑 scripts/run_phase0_poc.py 下载样本。")
        return 1

    rows = []
    for pdf_path in pdfs:
        code = pdf_path.stem.split("_")[0]
        try:
            result = parse_annual_report(pdf_path, stock_code=code)
            if not result.success:
                print(f"  ✗ {code} 解析失败: {result.error}")
                continue
            # 同一股票多条记录，取金额最大的
            best = max(result.records, key=lambda r: r.revenue_yuan or 0)
            rows.append(
                {
                    "stock_code": code,
                    "report_year": year,
                    "region_name": best.region_name,
                    "revenue": best.revenue,
                    "revenue_unit": best.revenue_unit,
                    "source_page": best.source_page,
                    "raw_text": best.raw_text,
                    "pdf_path": str(pdf_path),
                    # 额外记录：所有候选记录数和最大金额（元）
                    "_candidates_count": len(result.records),
                    "_revenue_yuan": best.revenue_yuan,
                }
            )
            print(
                f"  ✓ {code} {best.region_name}: {best.revenue:,.0f} {best.revenue_unit}"
                f" = {best.revenue_yuan / 1e8:,.1f} 亿元"
                f" (共 {len(result.records)} 条候选)"
            )
        except Exception as e:
            print(f"  ✗ {code} 异常: {type(e).__name__}: {e}")

    if not rows:
        print("\n✗ 没有成功解析的记录")
        return 1

    # 写 DuckDB
    store = DuckDBStore()
    try:
        # overseas_revenue 表已存在；只写入 schema 定义的列
        schema_cols = [
            "stock_code", "report_year", "region_name", "revenue",
            "revenue_unit", "source_page", "raw_text", "pdf_path",
        ]
        df = pd.DataFrame(rows)[schema_cols]
        n = store.save_overseas_revenue(df.to_dict("records"))
        print(f"\n✓ 入库 {n} 条记录")

        # 打印汇总
        print("\n  汇总:")
        for r in rows:
            print(
                f"    {r['stock_code']}: {r['_revenue_yuan'] / 1e8:>7.1f} 亿元"
                f"  ({r['region_name']}, 共 {r['_candidates_count']} 条候选)"
            )

        return 0
    finally:
        store.close()


if __name__ == "__main__":
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    sys.exit(main(year))
