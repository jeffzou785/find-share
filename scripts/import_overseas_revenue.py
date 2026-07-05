"""批量解析已下载的年报 PDF 并入库 overseas_revenue 表。

工作流：
1. 扫描 data/pdfs/annual_reports/ 下所有 PDF
2. 用 annual_report_parser 解析
3. P1-3 增强：
   - select_best_record 按 confidence + is_total_row 选最佳
   - 保留所有候选到 candidates_json
   - 多年交叉校验（同股票 N 年金额序列，差 >100x 标 parse_warning）
   - 写 parse_warning / confidence 字段
4. 入库 DuckDB overseas_revenue 表（带 parse_warning/confidence）

P1-3 不在这里做 ratio 校验（海外收入 / 总营收）；
策略层 overseas_champion.py 在评估时已经算了 overseas_ratio，
策略层的 sanity_check_yoy + overseas_ratio_max 已经能挡住绝大多数异常。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.collectors.annual_report_parser import (
    OverseasRevenueRecord,
    ParseResult,
    parse_annual_report,
    select_best_record,
)
from src.collectors.f10_overseas_revenue import fetch_mootdx_f10_overseas_revenue
from src.config import config
from src.storage import DuckDBStore
from src.utils.logging import configure_logging


# 多年金额序列异常阈值：N 年比 N-1 年差 CROSS_YEAR_FACTOR 倍 → 单位识别疑点
CROSS_YEAR_FACTOR = 100.0
logger = configure_logging(__name__)


def _log(message: str = "") -> None:
    """批量 PDF 解析耗时较长，输出必须即时刷新。"""
    logger.info(message)


def _record_to_candidate_dict(r: OverseasRevenueRecord) -> dict:
    return {
        "region_name": r.region_name,
        "revenue": r.revenue,
        "revenue_unit": r.revenue_unit,
        "revenue_yuan": r.revenue_yuan,
        "source_page": r.source_page,
        "raw_text": r.raw_text,
        "is_total_row": r.is_total_row,
        "confidence": r.confidence,
    }


def _parse_report_with_fallback(
    pdf_path: Path,
    *,
    code: str,
    year: int,
    enable_f10_fallback: bool = True,
) -> ParseResult:
    """Parse PDF first; if it fails, try mootdx F10 as a best-effort fallback."""
    result = parse_annual_report(pdf_path, stock_code=code)
    if result.success or not enable_f10_fallback:
        return result

    fallback = fetch_mootdx_f10_overseas_revenue(code, report_year=year)
    if fallback.success:
        fallback.parse_warnings.insert(
            0,
            f"pdf_parser_failed_then_f10_fallback:{result.error[:80]}",
        )
        return fallback

    result.error = (
        f"{result.error}; f10_fallback_failed={fallback.error}"
        if result.error else f"f10_fallback_failed={fallback.error}"
    )
    return result


def _cross_year_check(
    code: str, year: int, revenue_yuan: float,
    history: dict[int, float],
) -> list[str]:
    """同股票多年金额序列校验：N 年比 N-1 年差 >100x → 单位识别疑点。

    history: {year: revenue_yuan}（不含当前 year）
    """
    warnings: list[str] = []
    prev_year = year - 1
    if prev_year in history and history[prev_year] > 0:
        ratio = revenue_yuan / history[prev_year]
        if ratio > CROSS_YEAR_FACTOR:
            warnings.append(
                f"cross_year_unit_anomaly:{year}={revenue_yuan/1e8:.2f}yi "
                f"vs {prev_year}={history[prev_year]/1e8:.2f}yi ratio={ratio:.0f}x"
            )
        elif ratio < 1.0 / CROSS_YEAR_FACTOR:
            warnings.append(
                f"cross_year_unit_anomaly:{year}={revenue_yuan/1e8:.2f}yi "
                f"vs {prev_year}={history[prev_year]/1e8:.2f}yi ratio={ratio:.4f}x"
            )
    return warnings


def _load_history_from_store(store: DuckDBStore) -> dict[str, dict[int, float]]:
    """从 store 读现有的 overseas_revenue，构造 {code: {year: revenue_yuan}}。"""
    df = store.load_overseas_revenue()
    if df.empty:
        return {}
    history: dict[str, dict[int, float]] = {}
    for _, row in df.iterrows():
        unit_factor = {"元": 1.0, "千元": 1_000.0, "万元": 10_000.0,
                       "百万": 1_000_000.0, "亿元": 100_000_000.0}.get(
            row["revenue_unit"], 1.0
        )
        rev_yuan = float(row["revenue"]) * unit_factor
        if rev_yuan > 5e12:
            rev_yuan = rev_yuan / 1e4
        history.setdefault(row["stock_code"], {})[int(row["report_year"])] = rev_yuan
    return history


def _normalize_codes(raw_codes: str | None) -> set[str] | None:
    if not raw_codes:
        return None
    codes = {
        part.strip().zfill(6)
        for part in raw_codes.split(",")
        if part.strip()
    }
    return codes or None


def main(
    year: int = 2024,
    *,
    enable_f10_fallback: bool = True,
    codes: set[str] | None = None,
) -> int:
    pdf_dir = config.ANNUAL_REPORT_PDF_DIR
    _log(f"扫描目录: {pdf_dir}")
    # canonical: _annual_report.pdf；legacy: _annual.pdf（旧下载器产出）
    canonical = sorted(pdf_dir.glob(f"*_{year}_annual_report.pdf"))
    legacy = sorted(pdf_dir.glob(f"*_{year}_annual.pdf"))
    # 同一 code 同时存在两种命名时优先 canonical
    seen = {p.stem.split("_")[0] for p in canonical}
    legacy_only = [p for p in legacy if p.stem.split("_")[0] not in seen]
    pdfs = canonical + legacy_only
    if codes:
        pdfs = [p for p in pdfs if p.stem.split("_")[0] in codes]
    _log(
        f"找到 {len(pdfs)} 份年报 PDF"
        f"（canonical {len(canonical)} + legacy {len(legacy_only)}）\n"
    )

    if not pdfs:
        _log("✗ 没有找到 PDF。先跑 scripts/run_phase0_poc.py 下载样本。")
        return 1

    # 第一步：解析所有 PDF，得到候选记录（保留 candidates）
    parsed: list[dict] = []  # 待入库的行（含 candidates_json, parse_warning, confidence）
    parse_failures: list[tuple[str, str]] = []

    for pdf_path in pdfs:
        code = pdf_path.stem.split("_")[0]
        try:
            result = _parse_report_with_fallback(
                pdf_path,
                code=code,
                year=year,
                enable_f10_fallback=enable_f10_fallback,
            )
            if not result.success:
                parse_failures.append((code, result.error))
                _log(f"  ✗ {code} 解析失败: {result.error}")
                continue

            best, select_warnings = select_best_record(result.records)
            if best is None:
                parse_failures.append((code, "select_best_record returned None"))
                continue

            candidates_json = json.dumps(
                [_record_to_candidate_dict(r) for r in result.records],
                ensure_ascii=False,
            )
            parse_warnings = list(result.parse_warnings) + select_warnings
            parse_warning_str = "; ".join(parse_warnings) if parse_warnings else None

            parsed.append({
                "stock_code": code,
                "report_year": year,
                "region_name": best.region_name,
                "revenue": best.revenue,
                "revenue_unit": best.revenue_unit,
                "source_page": best.source_page,
                "raw_text": best.raw_text,
                "pdf_path": result.pdf_path if result.pdf_path.startswith("mootdx_f10:") else str(pdf_path),
                "candidates_json": candidates_json,
                "parse_warning": parse_warning_str,
                "confidence": best.confidence,
                "_revenue_yuan": best.revenue_yuan,
                "_candidates_count": len(result.records),
            })
            _log(
                f"  ✓ {code} {best.region_name}: {best.revenue:,.0f} {best.revenue_unit}"
                f" = {best.revenue_yuan / 1e8:,.1f} 亿元"
                f" (共 {len(result.records)} 条候选, confidence={best.confidence}"
                f"{', ' + parse_warning_str if parse_warning_str else ''})"
            )
        except Exception as e:
            parse_failures.append((code, f"{type(e).__name__}: {e}"))
            _log(f"  ✗ {code} 异常: {type(e).__name__}: {e}")

    if not parsed:
        _log("\n✗ 没有成功解析的记录")
        return 1

    # 第二步：多年交叉校验（需要 store）
    store = DuckDBStore()
    try:
        history = _load_history_from_store(store)
        # 把本次解析的也合并进 history，让后续年也能 cross check（多年同批入库场景）
        for row in parsed:
            history.setdefault(row["stock_code"], {})[row["report_year"]] = row["_revenue_yuan"]

        for row in parsed:
            code = row["stock_code"]
            yr = row["report_year"]
            # 临时把当前年从 history 排除（避免自比）
            curr_history = {k: v for k, v in history.get(code, {}).items() if k != yr}
            cross_warnings = _cross_year_check(code, yr, row["_revenue_yuan"], curr_history)
            if cross_warnings:
                existing = row["parse_warning"]
                merged = "; ".join(
                    ([existing] if existing else []) + cross_warnings
                )
                row["parse_warning"] = merged

        # 第三步：写 DuckDB（只写 schema 定义的列）
        schema_cols = [
            "stock_code", "report_year", "region_name", "revenue",
            "revenue_unit", "source_page", "raw_text", "pdf_path",
            "candidates_json", "parse_warning", "confidence",
        ]
        df = pd.DataFrame(parsed)[schema_cols]
        n = store.save_overseas_revenue(df.to_dict("records"))
        _log(f"\n✓ 入库 {n} 条记录")

        # 汇总
        n_warning = sum(1 for r in parsed if r["parse_warning"])
        n_high = sum(1 for r in parsed if r["confidence"] == "high")
        _log(f"\n  汇总:")
        _log(f"    总 {len(parsed)} 条；confidence: high={n_high}, "
             f"medium={sum(1 for r in parsed if r['confidence']=='medium')}, "
             f"low={sum(1 for r in parsed if r['confidence']=='low')}")
        _log(f"    含 parse_warning: {n_warning} 条")
        _log(f"    解析失败: {len(parse_failures)} 条")
        if parse_failures:
            for code, err in parse_failures[:10]:
                _log(f"      {code}: {err[:60]}")

        # 列出有 warning 的（高优先 review）
        if n_warning:
            _log(f"\n  ⚠ 需 review（含 parse_warning）:")
            for r in parsed:
                if r["parse_warning"]:
                    _log(f"    {r['stock_code']}: {r['_revenue_yuan']/1e8:.2f} 亿元 "
                         f"| {r['parse_warning']}")

        return 0
    finally:
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("year", type=int, nargs="?", default=2024)
    parser.add_argument(
        "--skip-f10-fallback",
        action="store_true",
        help="PDF 解析失败时不尝试 mootdx F10 主营构成 fallback",
    )
    parser.add_argument(
        "--codes",
        default=None,
        help="只导入指定股票，多个代码用逗号分隔，如 001311,002085",
    )
    args = parser.parse_args()
    sys.exit(main(
        args.year,
        enable_f10_fallback=not args.skip_f10_fallback,
        codes=_normalize_codes(args.codes),
    ))
