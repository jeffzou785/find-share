"""策略二A：集采修复型医药股 MVP。

只使用本地 DuckDB：
- stock_industry 生成医药候选池
- pharma_vbp_events 提供可追溯集采事件
- financials 验证收入/利润/毛利率/现金流修复
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.config import config
from src.screening import Status
from src.storage import DuckDBStore
from src.strategies.pharma_strategy import (
    PHARMA_VBP_STRATEGY,
    VbpRecoveryConfig,
    evaluate_vbp_recovery_batch,
)
from src.utils.logging import configure_logging


logger = configure_logging(__name__)


def _gen_run_id(period: str) -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{secrets.token_hex(2)}_{period}"


def _normalize_cli_yoy_threshold(value: float) -> float:
    """CLI 兼容：10 表示 10%，0.10 也表示 10%。"""
    return value / 100.0 if value > 1 else value


def _normalize_cli_percentile_threshold(value: float) -> float:
    """CLI 兼容：70 表示 70 分位，0.70 也表示 70 分位。"""
    return value * 100.0 if value <= 1 else value


def _load_candidates(
    store: DuckDBStore,
    *,
    codes: list[str] | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    candidates = store.load_stock_industry(sw_first=["医药生物"])
    if candidates.empty:
        return candidates
    candidates = candidates.copy()
    candidates["code"] = candidates["code"].astype(str).str.zfill(6)
    if codes:
        wanted = {str(c).zfill(6) for c in codes}
        candidates = candidates[candidates["code"].isin(wanted)]
    if limit:
        candidates = candidates.head(limit)
    return candidates.reset_index(drop=True)


def _write_outputs(run_id: str, period: str, results, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [r.to_row() for r in results]
    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(out_dir / f"pharma_vbp_{period}.csv", index=False, encoding="utf-8-sig")
    counts = Counter(r.status.value for r in results)
    lines = [
        f"# Pharma VBP Recovery Run {run_id}",
        "",
        f"- period: {period}",
        f"- total: {len(results)}",
        "",
        "| status | count |",
        "|---|---:|",
    ]
    for status in [s.value for s in Status]:
        lines.append(f"| {status} | {counts.get(status, 0)} |")

    hits = [r for r in results if r.status == Status.HIT]
    watches = [r for r in results if r.status == Status.WATCH]
    lines.extend(["", "## Hit / Watch", ""])
    lines.append("| code | name | status | reason |")
    lines.append("|---|---|---|---|")
    for r in hits + watches:
        reason = r.hit_reason or r.watch_reason or ""
        lines.append(f"| {r.code} | {r.name or ''} | {r.status.value} | {reason} |")

    rejected = Counter(r.reject_reason for r in results if r.reject_reason)
    missing = Counter(r.data_missing_reason for r in results if r.data_missing_reason)
    lines.extend(["", "## Rejected Reasons", ""])
    for reason, count in rejected.most_common():
        lines.append(f"- {reason}: {count}")
    lines.extend(["", "## Data Missing Reasons", ""])
    for reason, count in missing.most_common():
        lines.append(f"- {reason}: {count}")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run_screen(
    *,
    store: DuckDBStore,
    run_id: str,
    period: str,
    candidates: pd.DataFrame,
    config_obj: VbpRecoveryConfig,
):
    financials_by_code = {
        str(code).zfill(6): store.load_financials(str(code).zfill(6))
        for code in candidates["code"].astype(str).str.zfill(6).tolist()
    }
    pe_pb_history_by_code = {
        str(code).zfill(6): store.load_pe_pb_history(str(code).zfill(6))
        for code in candidates["code"].astype(str).str.zfill(6).tolist()
    }
    vbp_events = store.load_pharma_vbp_events()
    return evaluate_vbp_recovery_batch(
        candidates=candidates,
        financials_by_code=financials_by_code,
        vbp_events=vbp_events,
        pe_pb_history_by_code=pe_pb_history_by_code,
        run_id=run_id,
        period=period,
        config=config_obj,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--period", required=True, help="验证报告期，如 2026Q1/2025A")
    parser.add_argument("--codes", nargs="*", default=None, help="手动指定股票代码")
    parser.add_argument("--limit", type=int, default=None, help="处理上限")
    parser.add_argument(
        "--min-revenue-yoy",
        type=float,
        default=0.0,
        help="收入同比阈值，兼容小数或百分数：0.10/10 均表示 10%%",
    )
    parser.add_argument(
        "--min-net-profit-yoy",
        type=float,
        default=0.0,
        help="净利润同比阈值，兼容小数或百分数：0.10/10 均表示 10%%",
    )
    parser.add_argument(
        "--min-gross-margin-change",
        type=float,
        default=-0.005,
        help="毛利率同比变化阈值，小数口径：-0.005 表示 -0.5pct",
    )
    parser.add_argument(
        "--min-ocf-per-share",
        type=float,
        default=0.0,
        help="每股经营现金流阈值",
    )
    parser.add_argument(
        "--pb-percentile-soft-max",
        type=float,
        default=70.0,
        help="PB历史分位软约束阈值，兼容 70/0.70，超过后 hit 降级为 watch",
    )
    parser.add_argument(
        "--pb-percentile-years",
        type=int,
        choices=(3, 5, 10),
        default=5,
        help="PB历史分位回看窗口",
    )
    args = parser.parse_args()

    config_obj = VbpRecoveryConfig(
        min_revenue_yoy=_normalize_cli_yoy_threshold(args.min_revenue_yoy),
        min_net_profit_yoy=_normalize_cli_yoy_threshold(args.min_net_profit_yoy),
        min_gross_margin_change=args.min_gross_margin_change,
        min_ocf_per_share=args.min_ocf_per_share,
        pb_percentile_soft_max=_normalize_cli_percentile_threshold(
            args.pb_percentile_soft_max,
        ),
        pb_percentile_years=args.pb_percentile_years,
    )
    run_id = _gen_run_id(args.period)
    config_json = json.dumps({
        "strategy": PHARMA_VBP_STRATEGY,
        "period": args.period,
        "thresholds": config_obj.__dict__,
        "data_sources": {
            "candidates": "stock_industry",
            "financials": "financials",
            "vbp_events": "pharma_vbp_events",
        },
    }, ensure_ascii=False, default=str)

    with DuckDBStore() as store:
        candidates = _load_candidates(store, codes=args.codes, limit=args.limit)
        if candidates.empty:
            logger.error("✗ 无策略二A候选；请先刷新 stock_industry 或检查 --codes")
            return 1
        store.create_screen_run(
            run_id,
            PHARMA_VBP_STRATEGY,
            args.period,
            "pharma_vbp",
            config_json,
            hashlib.sha256(config_json.encode("utf-8")).hexdigest(),
            input_count=len(candidates),
        )
        try:
            results = run_screen(
                store=store,
                run_id=run_id,
                period=args.period,
                candidates=candidates,
                config_obj=config_obj,
            )
            rows = [r.to_row() for r in results]
            store.save_candidate_scores(rows)
            counts = Counter(r.status.value for r in results)
            run_status = "success" if not counts.get("error") else "partial_success"
            store.finish_screen_run(run_id, run_status, counts=dict(counts))
        except Exception as e:
            store.finish_screen_run(
                run_id, "failed", error=f"{type(e).__name__}: {e}",
            )
            raise

    out_dir = config.EXPORTS_DIR / "runs" / run_id
    _write_outputs(run_id, args.period, results, out_dir)
    logger.info(f"✓ run_id={run_id} counts={dict(counts)}")
    logger.info(f"  output: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
