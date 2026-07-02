"""P2：验证 screen_run 候选的下一期财务兑现情况。

默认读取最新 success/partial_success run，只使用本地 financials 表，不联网。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.config import config
from src.screening.financial_validation import (
    DEFAULT_VALIDATION_STATUSES,
    summarize_financial_validation,
    validate_next_financials_batch,
)
from src.storage import DuckDBStore
from src.utils.logging import configure_logging


logger = configure_logging(__name__)


def _resolve_run_id(
    store: DuckDBStore,
    *,
    run_id: str | None,
    strategy: str | None,
    period: str | None,
) -> str | None:
    if run_id:
        return run_id
    sql = (
        "SELECT run_id FROM screen_runs "
        "WHERE status IN ('success', 'partial_success')"
    )
    params: list[object] = []
    if strategy and strategy != "all":
        sql += " AND strategy = ?"
        params.append(strategy)
    if period:
        sql += " AND period = ?"
        params.append(period)
    sql += " ORDER BY started_at DESC LIMIT 1"
    row = store.conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _write_markdown(out_path: Path, *, run_id: str, summary: dict) -> None:
    lines = [
        "# Next Financial Validation",
        "",
        f"- run_id: `{run_id}`",
        f"- total_rows: {summary.get('total_rows', 0)}",
        "",
        "| verdict | rows |",
        "|---|---:|",
    ]
    for verdict, count in summary.get("verdicts", {}).items():
        lines.append(f"| {verdict} | {count} |")
    lines.extend([
        "",
        f"- avg_revenue_yoy: {summary.get('avg_revenue_yoy')}",
        f"- avg_net_profit_yoy: {summary.get('avg_net_profit_yoy')}",
    ])
    out_path.write_text("\n".join(lines), encoding="utf-8")


def run_validation(
    *,
    store: DuckDBStore,
    run_id: str,
    validation_period: str | None,
    statuses: list[str],
    min_revenue_yoy: float,
    min_net_profit_yoy: float,
) -> pd.DataFrame:
    candidates = store.load_candidate_scores(run_id)
    if candidates.empty:
        return pd.DataFrame()
    return validate_next_financials_batch(
        candidates=candidates,
        financials_loader=store.load_financials,
        validation_period=validation_period,
        statuses=statuses,
        min_revenue_yoy=min_revenue_yoy,
        min_net_profit_yoy=min_net_profit_yoy,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run-id", default=None, help="指定 screen_run；默认取最新成功 run")
    parser.add_argument("--strategy", default=None, help="筛选最新 run 时限定 screen_runs.strategy")
    parser.add_argument("--period", default=None, help="筛选最新 run 时限定报告期")
    parser.add_argument("--validation-period", default=None,
                        help="覆盖验证报告期；默认按 source period 推导下一期")
    parser.add_argument("--statuses", nargs="*", default=list(DEFAULT_VALIDATION_STATUSES),
                        help="参与验证的 candidate status，默认 hit watch")
    parser.add_argument("--min-revenue-yoy", type=float, default=0.0,
                        help="收入同比确认阈值，小数口径，如 0.05 表示 5%")
    parser.add_argument("--min-net-profit-yoy", type=float, default=0.0,
                        help="净利润同比确认阈值，小数口径，如 0.10 表示 10%")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="输出目录，默认 data/exports/financial_validations/<run_id>",
    )
    args = parser.parse_args()

    with DuckDBStore() as store:
        run_id = _resolve_run_id(
            store, run_id=args.run_id, strategy=args.strategy, period=args.period,
        )
        if not run_id:
            logger.error("✗ 找不到可验证的 screen_run")
            return 1
        results = run_validation(
            store=store,
            run_id=run_id,
            validation_period=args.validation_period,
            statuses=args.statuses,
            min_revenue_yoy=args.min_revenue_yoy,
            min_net_profit_yoy=args.min_net_profit_yoy,
        )
        saved = (
            store.save_financial_validation_results(results)
            if not results.empty else 0
        )

    out_dir = Path(args.output_dir) if args.output_dir else (
        config.EXPORTS_DIR / "financial_validations" / run_id
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    results.to_csv(out_dir / "next_financials.csv", index=False, encoding="utf-8-sig")
    summary = summarize_financial_validation(results)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _write_markdown(out_dir / "summary.md", run_id=run_id, summary=summary)

    logger.info(f"✓ financial validation run_id={run_id} rows={len(results)} saved={saved}")
    logger.info(f"  output: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
