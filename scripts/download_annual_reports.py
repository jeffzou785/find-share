"""批量下载定期报告 PDF（年报 / 半年报 / 一季报 / 三季报）。

用法：
    python scripts/download_annual_reports.py 600031 600660 002594   # 指定股票
    python scripts/download_annual_reports.py --extension            # 下载扩展池
    python scripts/download_annual_reports.py --year 2024            # 指定年份
    python scripts/download_annual_reports.py 600519 --report-type half_year --year 2025
    python scripts/download_annual_reports.py 600519 --report-type q1 --year 2025
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.collectors.cninfo_downloader import CnInfoDownloader


REPORT_TYPE_CN = {
    "annual": "年报",
    "half_year": "半年报",
    "q1": "一季报",
    "q3": "三季报",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="批量下载定期报告 PDF")
    parser.add_argument("codes", nargs="*", help="股票代码（如 600031 600660）")
    parser.add_argument("--extension", action="store_true", help="从扩展池文件读取代码")
    parser.add_argument("--year", type=int, default=2024, help="报告年份（默认 2024）")
    parser.add_argument(
        "--report-type",
        default="annual",
        choices=list(REPORT_TYPE_CN),
        help="报告类型（默认 annual）",
    )
    parser.add_argument("--limit", type=int, default=0, help="最多下载几只（0=不限制）")
    args = parser.parse_args()

    codes: list[str] = list(args.codes)

    if args.extension:
        ext_path = PROJECT_ROOT / "data" / "exports" / "overseas_extension_candidates.csv"
        if not ext_path.exists():
            print(f"✗ 扩展池文件不存在: {ext_path}")
            print("  请先运行 run_phase3_strategy3.py 生成")
            return 1
        df = pd.read_csv(ext_path, dtype={"code": str})
        codes.extend(df["code"].tolist())
        print(f"从扩展池读取 {len(df)} 只候选")

    if not codes:
        parser.print_help()
        return 1

    if args.limit > 0:
        codes = codes[: args.limit]

    codes = [c.zfill(6) for c in codes]
    type_cn = REPORT_TYPE_CN[args.report_type]
    print(f"\n准备下载 {len(codes)} 只股票的 {args.year} {type_cn}")

    downloader = CnInfoDownloader()
    success = 0
    failed = []

    for i, code in enumerate(codes, 1):
        print(f"[{i}/{len(codes)}] {code}...", end=" ")
        try:
            pdf_path = downloader.download_report(
                code, year=args.year, report_type=args.report_type
            )
            size_kb = pdf_path.stat().st_size // 1024
            print(f"✓ {size_kb} KB")
            success += 1
        except Exception as e:
            print(f"✗ {type(e).__name__}: {str(e)[:60]}")
            failed.append((code, str(e)))
        time.sleep(1.5)  # 友善限速

    print(f"\n汇总: ✓ {success} / {len(codes)}")
    if failed:
        print(f"失败 {len(failed)} 只:")
        for code, err in failed:
            print(f"  - {code}: {err}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
