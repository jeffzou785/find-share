"""策略三海外收入 parser 质量池导出。

用途：
- 汇总 overseas_revenue 表中的 parse_warning / 低置信度记录。
- 找出本地已有年报 PDF、但指定年份未解析入库的股票。
- 结合最近一次海外筛选 run，补充 overseas_revenue_missing / parse_warning 候选。

默认输出：
- data/exports/overseas_parser_quality_review.md
- data/exports/overseas_parser_quality_review.csv
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.config import config
from src.storage import DuckDBStore


DEFAULT_MD = PROJECT_ROOT / "data/exports/overseas_parser_quality_review.md"
DEFAULT_CSV = PROJECT_ROOT / "data/exports/overseas_parser_quality_review.csv"
VERIFIED_PURE_DOMESTIC_CSV = PROJECT_ROOT / "tests/fixtures/verified_pure_domestic.csv"
UNIT_FACTOR = {
    "元": 1.0,
    "千元": 1_000.0,
    "万元": 10_000.0,
    "百万": 1_000_000.0,
    "亿元": 100_000_000.0,
}
PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2}
ISSUE_COLUMNS = [
    "priority", "issue_type", "code", "name", "year", "confidence",
    "revenue_yi", "run_id", "pdf_path", "evidence", "next_action",
]


def _non_empty(value: Any) -> bool:
    return value is not None and pd.notna(value) and str(value).strip() != ""


def _load_verified_pure_domestic(year: int) -> set[str]:
    """加载已人工核验为纯内销的 code 集合（parser-review 跳过这些 P3 噪音）。

    CSV schema: code,name,year,reason —— 同一 code 不同年份需分别核验。
    """
    if not VERIFIED_PURE_DOMESTIC_CSV.exists():
        return set()
    try:
        df = pd.read_csv(VERIFIED_PURE_DOMESTIC_CSV, dtype=str)
    except Exception:
        return set()
    if df.empty or "code" not in df.columns or "year" not in df.columns:
        return set()
    return {
        str(r["code"]).zfill(6)
        for _, r in df.iterrows()
        if str(r.get("year")).strip() == str(year)
    }


def _truncate(value: Any, limit: int = 100) -> str:
    text = "" if value is None or pd.isna(value) else str(value)
    text = text.replace("\n", " ").replace("|", "/").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _pdf_code(path: Path) -> str:
    return path.stem.split("_")[0].zfill(6)


def find_annual_report_pdfs(pdf_dir: Path, year: int) -> dict[str, Path]:
    """返回 {code: pdf_path}，同 code 优先 canonical 文件名。"""
    canonical = sorted(pdf_dir.glob(f"*_{year}_annual_report.pdf"))
    legacy = sorted(pdf_dir.glob(f"*_{year}_annual.pdf"))
    out = {_pdf_code(path): path for path in canonical}
    for path in legacy:
        out.setdefault(_pdf_code(path), path)
    return out


def _revenue_yi(row: pd.Series) -> float | None:
    try:
        factor = UNIT_FACTOR.get(str(row.get("revenue_unit")), 1.0)
        revenue_yuan = float(row.get("revenue")) * factor
        if revenue_yuan > 5e12:
            revenue_yuan = revenue_yuan / 1e4
        return revenue_yuan / 1e8
    except (TypeError, ValueError):
        return None


def _candidate_count(candidates_json: Any) -> int | None:
    if not _non_empty(candidates_json):
        return None
    try:
        parsed = json.loads(str(candidates_json))
    except json.JSONDecodeError:
        return None
    return len(parsed) if isinstance(parsed, list) else None


def _latest_overseas_run_id(store: DuckDBStore, period: str) -> str | None:
    runs = store.list_screen_runs(strategy="overseas", period=period, limit=20)
    if runs.empty:
        return None
    valid = runs[runs["status"].isin(["success", "partial_success"])]
    if valid.empty:
        valid = runs
    return str(valid.iloc[0]["run_id"])


def _classify_missing_pdf(code: str, pdf_path: Path) -> tuple[str, str, str]:
    """P1.5-7：单股跑一次 parser，按 result.error 细分 issue 类型。

    返回 (issue_type, priority, evidence)：
    - no_overseas_section / P3：PDF 真无分地区附注（公司可能纯内销）
    - pure_domestic / P3      ：有分地区附注但全表无境外关键词（公司纯内销）
    - parse_failure / P1     ：找到附注且含境外关键词但未提取
    - pdf_corrupt / P1       ：pdfplumber 异常
    - pdf_without_parsed_overseas / P1：兜底（其他错误）
    """
    try:
        from src.collectors.annual_report_parser import (
            OVERSEAS_KEYWORDS, parse_annual_report,
        )
        result = parse_annual_report(Path(pdf_path), stock_code=code)
    except Exception as e:
        return ("pdf_corrupt", "P1", f"parse_annual_report 抛异常: {type(e).__name__}: {e}")
    if result.success:
        # 解析成功但 DB 没记录 → 历史漏入库，重跑 import-overseas 即可
        return ("pdf_without_parsed_overseas", "P1",
                "本地能解析但未入库，重跑 import-overseas --codes")
    err = result.error or ""
    if "未找到分地区营业收入附注" in err:
        return ("no_overseas_section", "P3",
                "PDF 未找到分地区营业收入附注，可能该公司无境外业务")
    if "找到附注但未提取到境外数据" in err:
        # 进一步检查：分地区表里是否真有境外关键词？
        # 如果整个 PDF 都无境外词，是纯内销公司，不是 parser bug
        try:
            import pdfplumber
            with pdfplumber.open(Path(pdf_path)) as pdf:
                full_text = ""
                for page in pdf.pages:
                    full_text += page.extract_text() or ""
            has_overseas_word = any(kw in full_text for kw in OVERSEAS_KEYWORDS if kw != "国际")
            # 排除"境外会计准则"这种无关上下文
            has_overseas_word = (
                has_overseas_word
                and "境外会计准则" not in full_text.replace(" ", "")
            )
        except Exception:
            has_overseas_word = True  # 解析失败时保守归类为 P1
        if not has_overseas_word:
            return ("pure_domestic", "P3",
                    "PDF 含分地区附注但全文无境外业务关键词，判定为纯内销公司")
        return ("parse_failure", "P1", err)
    return ("pdf_without_parsed_overseas", "P1", err or "未知错误")


def _issue(
    *,
    code: str,
    issue_type: str,
    priority: str,
    year: int,
    evidence: str,
    next_action: str,
    name: str | None = None,
    confidence: str | None = None,
    revenue_yi: float | None = None,
    run_id: str | None = None,
    pdf_path: str | None = None,
) -> dict:
    return {
        "priority": priority,
        "issue_type": issue_type,
        "code": str(code).zfill(6),
        "name": name,
        "year": year,
        "confidence": confidence,
        "revenue_yi": revenue_yi,
        "run_id": run_id,
        "pdf_path": pdf_path,
        "evidence": evidence,
        "next_action": next_action,
    }


def build_quality_review(
    store: DuckDBStore,
    *,
    year: int,
    period: str | None = None,
    pdf_dir: Path | None = None,
    run_id: str | None = None,
) -> dict:
    """构造策略三 parser 质量池。"""
    period = period or f"{year}A"
    pdf_dir = pdf_dir or config.ANNUAL_REPORT_PDF_DIR
    pdfs = find_annual_report_pdfs(pdf_dir, year)
    verified_pure_domestic = _load_verified_pure_domestic(year)

    overseas = store.load_overseas_revenue()
    if overseas.empty:
        year_rows = pd.DataFrame()
    else:
        year_rows = overseas[overseas["report_year"].astype(int) == int(year)].copy()

    issues: list[dict] = []
    parsed_codes = set()
    if not year_rows.empty:
        year_rows["stock_code"] = year_rows["stock_code"].astype(str).str.zfill(6)
        parsed_codes = set(year_rows["stock_code"])
        for _, row in year_rows.iterrows():
            code = str(row["stock_code"]).zfill(6)
            confidence = row.get("confidence")
            revenue_yi = _revenue_yi(row)
            warning = row.get("parse_warning")
            candidates = _candidate_count(row.get("candidates_json"))
            if _non_empty(warning):
                issues.append(_issue(
                    code=code,
                    issue_type="parse_warning",
                    priority="P1",
                    year=year,
                    confidence=str(confidence) if _non_empty(confidence) else None,
                    revenue_yi=revenue_yi,
                    pdf_path=str(row.get("pdf_path")) if _non_empty(row.get("pdf_path")) else None,
                    evidence=_truncate(warning, 180),
                    next_action="加入 golden case，复核单位/地区行/候选选择后重跑 import-overseas",
                ))
            if str(confidence).strip().lower() != "high":
                issues.append(_issue(
                    code=code,
                    issue_type="low_confidence",
                    priority="P2",
                    year=year,
                    confidence=str(confidence) if _non_empty(confidence) else None,
                    revenue_yi=revenue_yi,
                    pdf_path=str(row.get("pdf_path")) if _non_empty(row.get("pdf_path")) else None,
                    evidence=f"confidence={confidence}; candidates={candidates}",
                    next_action="人工核对分地区附注；若误判，补关键词/表格结构测试",
                ))

    for code, path in pdfs.items():
        if code not in parsed_codes:
            # Phase H：跳过已人工核验为纯内销的 code（避免占 issue 数）
            if code in verified_pure_domestic:
                continue
            # 区分"无境外业务（噪声）"vs"parser 失败（P1）"：跑一次单股解析看 error。
            issue_type, priority, evidence = _classify_missing_pdf(code, path)
            next_action = {
                "no_overseas_section": "PDF 无分地区附注，确认该公司无境外业务，可忽略",
                "pure_domestic": "PDF 有分地区表但全文无境外业务关键词，公司纯内销，可忽略",
                "parse_failure": "parser 找到附注但未提取，加入 golden case 后重跑 import-overseas",
                "pdf_corrupt": "PDF 解析异常，检查 pdfplumber 兼容性或重新下载",
                "pdf_without_parsed_overseas": "单股重跑 import-overseas --codes；失败样本写入 golden case",
            }[issue_type]
            issues.append(_issue(
                code=code,
                issue_type=issue_type,
                priority=priority,
                year=year,
                pdf_path=str(path),
                evidence=evidence,
                next_action=next_action,
            ))

    run_id = run_id or _latest_overseas_run_id(store, period)
    scores = store.load_candidate_scores(run_id) if run_id else pd.DataFrame()
    if not scores.empty:
        for _, row in scores.iterrows():
            code = str(row.get("code")).zfill(6)
            if row.get("data_missing_reason") == "overseas_revenue_missing":
                pdf_path = str(pdfs.get(code)) if code in pdfs else None
                issues.append(_issue(
                    code=code,
                    name=row.get("name"),
                    issue_type="screen_overseas_revenue_missing",
                    priority="P1",
                    year=year,
                    run_id=run_id,
                    pdf_path=pdf_path,
                    evidence="最新海外筛选因 overseas_revenue_missing 进入 data_missing",
                    next_action="优先补该股年报海外收入；若无年报附注，标注为非 parser 问题",
                ))
            if row.get("watch_reason") == "parse_warning":
                evidence = "筛选侧 watch_reason=parse_warning"
                raw_metrics = row.get("metrics_json")
                try:
                    metrics = (
                        json.loads(str(raw_metrics))
                        if _non_empty(raw_metrics) else {}
                    )
                    evidence = metrics.get("overseas", {}).get("parse_warning") or evidence
                except (TypeError, json.JSONDecodeError):
                    pass
                issues.append(_issue(
                    code=code,
                    name=row.get("name"),
                    issue_type="screen_parse_warning",
                    priority="P1",
                    year=year,
                    run_id=run_id,
                    pdf_path=str(pdfs.get(code)) if code in pdfs else None,
                    evidence=_truncate(evidence, 180),
                    next_action="从筛选 metrics 反查 parser warning，补 golden case 后重跑筛选",
                ))

    issue_df = pd.DataFrame(issues, columns=ISSUE_COLUMNS)
    if not issue_df.empty:
        issue_df["_priority_order"] = issue_df["priority"].map(PRIORITY_ORDER).fillna(99)
        issue_df = issue_df.sort_values(
            ["_priority_order", "issue_type", "code"]
        ).drop(columns=["_priority_order"]).reset_index(drop=True)

    summary = {
        "year": year,
        "period": period,
        "run_id": run_id,
        "pdf_count": len(pdfs),
        "parsed_count": len(parsed_codes),
        "issue_count": len(issue_df),
        "verified_pure_domestic_count": len(verified_pure_domestic),
    }
    return {"summary": summary, "issues": issue_df}


def write_review(review: dict, *, md_path: Path = DEFAULT_MD, csv_path: Path = DEFAULT_CSV) -> None:
    issues: pd.DataFrame = review["issues"]
    summary = review["summary"]
    md_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    issues.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines = [
        "# 策略三海外收入 Parser 质量池",
        "",
        f"- 年份：{summary['year']}",
        f"- 期间：{summary['period']}",
        f"- 关联 run_id：{summary['run_id'] or '无'}",
        f"- 本地年报 PDF：{summary['pdf_count']} 份",
        f"- 已入库海外收入：{summary['parsed_count']} 只",
        f"- 待复核问题：{summary['issue_count']} 条",
        f"- 已跳过纯内销（人工核验）：{summary.get('verified_pure_domestic_count', 0)} 只",
        "",
        "## 问题分布",
        "",
    ]
    if issues.empty:
        lines.append("未发现 parser 质量问题。")
    else:
        priority_counts = issues["priority"].value_counts().to_dict()
        type_counts = issues["issue_type"].value_counts().to_dict()
        lines.extend(
            [
                f"- {k}: {priority_counts[k]}"
                for k in sorted(priority_counts, key=lambda p: PRIORITY_ORDER.get(p, 99))
            ]
        )
        lines.append("")
        lines.extend([f"- {k}: {v}" for k, v in sorted(type_counts.items())])
        lines.extend([
            "",
            "## 明细",
            "",
            "| priority | issue_type | code | name | confidence | revenue_yi | evidence | next_action |",
            "|---|---|---|---|---|---:|---|---|",
        ])
        for _, row in issues.iterrows():
            revenue = row.get("revenue_yi")
            revenue_text = "" if pd.isna(revenue) else f"{float(revenue):.2f}"
            lines.append(
                "| {priority} | {issue_type} | {code} | {name} | {confidence} | "
                "{revenue} | {evidence} | {next_action} |".format(
                    priority=row.get("priority") or "",
                    issue_type=row.get("issue_type") or "",
                    code=row.get("code") or "",
                    name=_truncate(row.get("name"), 40),
                    confidence=_truncate(row.get("confidence"), 40),
                    revenue=revenue_text,
                    evidence=_truncate(row.get("evidence"), 140),
                    next_action=_truncate(row.get("next_action"), 100),
                )
            )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--year", type=int, default=2025, help="年报年份")
    parser.add_argument("--period", default=None, help="筛选 period，默认 <year>A")
    parser.add_argument("--run-id", default=None, help="指定海外筛选 run_id")
    parser.add_argument("--pdf-dir", type=Path, default=None, help="年报 PDF 目录")
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args()

    store = DuckDBStore()
    try:
        review = build_quality_review(
            store,
            year=args.year,
            period=args.period,
            pdf_dir=args.pdf_dir,
            run_id=args.run_id,
        )
        write_review(review, md_path=args.output_md, csv_path=args.output_csv)
        summary = review["summary"]
        print(
            "✓ 已导出策略三 parser 质量池："
            f"{args.output_md} / {args.output_csv}\n"
            f"  year={summary['year']} pdf={summary['pdf_count']} "
            f"parsed={summary['parsed_count']} issues={summary['issue_count']}"
        )
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
