"""基于 `$a-stock-data` 口径补齐 A 股筛选数据。

默认补：
- 财务/三表：新浪三表，覆盖最近 8 期（包含 2026Q1 若已披露）
- 估值快照：腾讯 PE/PB/市值当前快照（不伪造历史分位）
- 年报 PDF：2024A + 2025A（用于 2025A 海外收入同比）
- 2026Q1 PDF：仅下载归档，不解析海外收入
- 研报 PDF：东财 reportapi + PDF + RAG（可选）

不补 2023；如传入 2023 会直接报错。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import import_overseas_revenue
from scripts.import_research_reports import ingest_one_stock
from src.collectors.a_stock_skill_source import (
    AStockSkillSource,
    financials_full_to_abstract,
)
from src.collectors.base import normalize_code
from src.collectors.cninfo_downloader import CnInfoDownloader
from src.storage import DuckDBStore
from src.utils.logging import configure_logging


logger = configure_logging(__name__)
DEFAULT_ANNUAL_YEARS = (2024, 2025)
DEFAULT_Q1_YEAR = 2026


def _normalize_codes(codes: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for code in codes:
        c = normalize_code(code)
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _codes_from_run(run_id: str) -> list[str]:
    path = PROJECT_ROOT / "data" / "exports" / "runs" / run_id / "data_missing.csv"
    if not path.exists():
        raise FileNotFoundError(f"data_missing.csv 不存在: {path}")
    df = pd.read_csv(path, dtype={"code": str})
    return _normalize_codes(df["code"].dropna().astype(str).tolist())


def _resolve_codes(args: argparse.Namespace, store: DuckDBStore) -> list[str]:
    if args.codes:
        codes = _normalize_codes(args.codes)
    elif args.from_run:
        codes = _codes_from_run(args.from_run)
    else:
        pool = store.load_stock_industry()
        if pool.empty:
            raise RuntimeError("stock_industry 为空，请先 bootstrap")
        codes = _normalize_codes(pool["code"].astype(str).tolist())

    if args.limit and args.limit > 0:
        codes = codes[: args.limit]
    if not codes:
        raise RuntimeError("没有可刷新代码")
    return codes


def _validate_years(years: list[int]) -> list[int]:
    unique = sorted(set(int(y) for y in years))
    if 2023 in unique:
        raise ValueError("本补数流程按要求不处理 2023 年报，请改用 2024/2025")
    return unique


def _validate_q1_year(year: int | None) -> int | None:
    if year is None or year == 0:
        return None
    year = int(year)
    if year == 2023:
        raise ValueError("本补数流程按要求不处理 2023Q1，请使用 2026Q1")
    return year


def refresh_financials(store: DuckDBStore, source: AStockSkillSource, codes: list[str]) -> dict[str, int]:
    counts = {
        "financials_ok": 0,
        "financials_full_rows": 0,
        "valuation_snapshot_ok": 0,
        "error": 0,
    }
    for code in codes:
        try:
            pe = source.get_pe_pb_history(code, years=10)
            if not pe.empty:
                store.save_pe_pb_history(code, pe)
                counts["valuation_snapshot_ok"] += 1
        except Exception as e:
            logger.warning(f"{code} 腾讯估值快照失败: {type(e).__name__}: {str(e)[:80]}")
            counts["error"] += 1

        try:
            full = source.get_financials_full(code, num=9)
            if not full.empty:
                counts["financials_full_rows"] += store.save_financials_full(full)
            abstract = financials_full_to_abstract(full)
            if not abstract.empty:
                store.save_financials(code, abstract)
                counts["financials_ok"] += 1
        except Exception as e:
            logger.warning(f"{code} 新浪财务失败: {type(e).__name__}: {str(e)[:80]}")
            counts["error"] += 1
        time.sleep(0.2)
    return counts


def download_reports(codes: list[str], annual_years: list[int], q1_year: int | None) -> dict[str, int]:
    downloader = CnInfoDownloader()
    counts = {"annual_ok": 0, "q1_ok": 0, "error": 0}
    for code in codes:
        for year in annual_years:
            try:
                downloader.download_report(code, year=year, report_type="annual")
                counts["annual_ok"] += 1
            except Exception as e:
                logger.warning(f"{code} {year} 年报下载失败: {type(e).__name__}: {str(e)[:80]}")
                counts["error"] += 1
            time.sleep(1.5)

        if q1_year is not None:
            try:
                downloader.download_report(code, year=q1_year, report_type="q1")
                counts["q1_ok"] += 1
            except Exception as e:
                logger.warning(f"{code} {q1_year}Q1 下载失败: {type(e).__name__}: {str(e)[:80]}")
                counts["error"] += 1
            time.sleep(1.5)
    return counts


def refresh_research_reports(
    store: DuckDBStore,
    codes: list[str],
    *,
    max_pages: int,
    max_pdfs: int,
    skip_rag: bool,
) -> dict[str, int]:
    total = {"reports_fetched": 0, "reports_saved": 0, "pdf_downloaded": 0, "rag_ingested": 0}
    for code in codes:
        stats = ingest_one_stock(
            code=code,
            max_pages=max_pages,
            skip_ths=False,
            skip_pdf=False,
            skip_rag=skip_rag,
            max_pdfs=max_pdfs,
            store=store,
        )
        for key in total:
            total[key] += int(stats.get(key, 0))
        time.sleep(1.0)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--codes", nargs="*", default=None, help="手动指定股票代码")
    parser.add_argument("--from-run", default=None, help="读取指定 run 的 data_missing.csv")
    parser.add_argument("--limit", type=int, default=None, help="处理上限")
    parser.add_argument(
        "--annual-years",
        type=int,
        nargs="*",
        default=list(DEFAULT_ANNUAL_YEARS),
        help="需下载并解析海外收入的年报年份，默认 2024 2025；禁止 2023",
    )
    parser.add_argument(
        "--q1-year",
        type=int,
        default=DEFAULT_Q1_YEAR,
        help="需下载的一季报年份，默认 2026；传 0 跳过",
    )
    parser.add_argument("--skip-financials", action="store_true", help="跳过腾讯/新浪补财务")
    parser.add_argument("--download-pdfs", action="store_true", help="下载年报 + 2026Q1 PDF")
    parser.add_argument("--parse-overseas", action="store_true", help="解析 annual-years 年报海外收入")
    parser.add_argument("--include-reports", action="store_true", help="下载研报 PDF 并入 RAG")
    parser.add_argument("--max-report-pages", type=int, default=1, help="东财研报页数")
    parser.add_argument("--max-report-pdfs", type=int, default=3, help="每只股票最多研报 PDF 数")
    parser.add_argument("--skip-rag", action="store_true", help="研报 PDF 下载后不入 RAG")
    args = parser.parse_args()

    try:
        annual_years = _validate_years(args.annual_years)
        q1_year = _validate_q1_year(args.q1_year)
    except ValueError as e:
        logger.error(str(e))
        return 2

    store = DuckDBStore()
    try:
        codes = _resolve_codes(args, store)
        logger.info(f"待补数据股票 {len(codes)} 只: {codes[:10]}{'...' if len(codes) > 10 else ''}")
        logger.info(f"annual_years={annual_years} q1_year={q1_year}")

        if not args.skip_financials:
            source = AStockSkillSource()
            stats = refresh_financials(store, source, codes)
            logger.info(f"财务/估值补齐: {stats}")

        if args.download_pdfs:
            stats = download_reports(codes, annual_years, q1_year)
            logger.info(f"公告 PDF 下载: {stats}")

        if args.parse_overseas:
            for year in annual_years:
                logger.info(f"解析海外收入: {year}")
                import_overseas_revenue.main(year)

        if args.include_reports:
            stats = refresh_research_reports(
                store,
                codes,
                max_pages=args.max_report_pages,
                max_pdfs=args.max_report_pdfs,
                skip_rag=args.skip_rag,
            )
            logger.info(f"研报补齐: {stats}")

        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
