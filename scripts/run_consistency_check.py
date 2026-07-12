"""P2-4 财报 vs 研报一致性校验 CLI。

对一批股票跑一致性校验，输出 markdown 报告：
- EPS 预测 vs 实际偏离超阈值
- 海外收入：财报有数据但研报未提及 / 财报无数据但研报说有
- 研报订单/区域信号（轻量）

用法：
    python3 scripts/run_consistency_check.py --run-id <run_id> --report-year 2025
    python3 scripts/run_consistency_check.py --codes 600031,001325 --report-year 2025
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import config
from src.screening.consistency import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARN,
    check_consistency_batch,
)
from src.storage import DuckDBStore


def _load_codes_from_run(store: DuckDBStore, run_id: str) -> list[str]:
    """从 screen_run 的 candidate_scores 拉 codes（仅 hit/watch）。"""
    df = store.load_candidate_scores(run_id)
    if df.empty:
        return []
    df = df[df["status"].isin(["hit", "watch"])]
    return sorted(df["code"].astype(str).str.zfill(6).unique().tolist())


def _render_report(
    results: dict, *, run_id: str | None, report_year: int
) -> str:
    """渲染 markdown 报告。"""
    total = len(results)
    warn_count = sum(1 for r in results.values() if r.has_warning)
    err_count = sum(1 for r in results.values() if r.has_error)
    lines = [
        "# 财报 vs 研报一致性校验",
        "",
        f"- 报告年份：{report_year}",
        f"- run_id：{run_id or '(none)'}",
        f"- 校验股票数：{total}",
        f"- warn 级：{warn_count} 只",
        f"- error 级：{err_count} 只",
        "",
    ]

    if not results:
        lines.append("_(无校验样本)_")
        return "\n".join(lines)

    # error 优先，其次 warn，info 单独
    def _severity_rank(s: str) -> int:
        return {"error": 0, "warn": 1, "info": 2}.get(s, 99)

    for code in sorted(results.keys()):
        r = results[code]
        if not r.observations:
            continue
        # 跳过只有 info 的报告（避免噪音）
        if not r.has_warning and not r.has_error:
            continue
        lines.append(f"## {code}")
        lines.append("")
        lines.append("| severity | kind | message |")
        lines.append("|------|------|------|")
        for obs in sorted(r.observations, key=lambda o: _severity_rank(o.severity)):
            emoji = {"error": "❌", "warn": "⚠", "info": "ℹ"}.get(obs.severity, "")
            lines.append(
                f"| {emoji} {obs.severity} | {obs.kind} | {obs.message} |"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--run-id", default=None,
        help="从指定 run_id 的 hit/watch 候选跑校验",
    )
    parser.add_argument(
        "--codes", default=None,
        help="显式指定 codes（逗号分隔），优先于 --run-id",
    )
    parser.add_argument(
        "--report-year", type=int, default=2025,
        help="报告年份（默认 2025）",
    )
    parser.add_argument(
        "--output", default=None,
        help="Markdown 输出路径（默认 data/exports/consistency_report.md）",
    )
    args = parser.parse_args()

    if not args.codes and not args.run_id:
        print("✗ 需要 --codes 或 --run-id")
        return 1

    store = DuckDBStore()
    try:
        if args.codes:
            codes = [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()]
        else:
            codes = _load_codes_from_run(store, args.run_id)
            if not codes:
                print(f"✗ run_id={args.run_id} 无 hit/watch 候选")
                return 1

        results = check_consistency_batch(store, codes, args.report_year)
        md = _render_report(results, run_id=args.run_id, report_year=args.report_year)

        out_path = Path(args.output) if args.output else (
            config.EXPORTS_DIR / "consistency_report.md"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")

        warn_count = sum(1 for r in results.values() if r.has_warning)
        err_count = sum(1 for r in results.values() if r.has_error)
        print(
            f"✓ 校验 {len(results)} 只 → warn={warn_count} / error={err_count}；"
            f"报告：{out_path}"
        )
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
