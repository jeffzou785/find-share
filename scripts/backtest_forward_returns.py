"""P2：对 screen_run 候选做 20/60/120 交易日前瞻收益验证。

默认读取最新 success/partial_success run，只使用本地 pe_pb_history 表，不联网。
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
from src.screening.backtest import (
    DEFAULT_STATUSES,
    DEFAULT_WINDOWS,
    compute_forward_returns_batch,
    normalize_windows,
    summarize_backtest,
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


def _load_anchor_date(
    store: DuckDBStore,
    run_id: str,
    override: str | None = None,
) -> pd.Timestamp | None:
    if override:
        return pd.to_datetime(override).normalize()
    run = store.load_screen_run(run_id)
    if run.empty:
        return None
    value = run.iloc[0].get("started_at")
    if value is None or pd.isna(value):
        return None
    return pd.to_datetime(value).normalize()


def _write_markdown(out_path: Path, *, run_id: str, summary: dict) -> None:
    lines = [
        "# Forward Return Backtest",
        "",
        f"- run_id: `{run_id}`",
        f"- total_rows: {summary.get('total_rows', 0)}",
        "",
        "| window_days | rows | ok_rows | missing_rows | avg_absolute_return | avg_relative_return |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for window, info in summary.get("windows", {}).items():
        lines.append(
            f"| {window} | {info.get('rows')} | {info.get('ok_rows')} | "
            f"{info.get('missing_rows')} | {info.get('avg_absolute_return')} | "
            f"{info.get('avg_relative_return')} |"
        )
    out_path.write_text("\n".join(lines), encoding="utf-8")


def run_backtest(
    *,
    store: DuckDBStore,
    run_id: str,
    anchor_date: pd.Timestamp,
    windows: list[int],
    statuses: list[str],
    benchmark_code: str | None,
    max_start_lag_days: int,
) -> pd.DataFrame:
    candidates = store.load_candidate_scores(run_id)
    if candidates.empty:
        return pd.DataFrame()
    benchmark_history = (
        store.load_pe_pb_history(str(benchmark_code).zfill(6))
        if benchmark_code else None
    )
    return compute_forward_returns_batch(
        candidates=candidates,
        anchor_date=anchor_date,
        price_loader=store.load_pe_pb_history,
        windows=windows,
        benchmark_history=benchmark_history,
        benchmark_code=str(benchmark_code).zfill(6) if benchmark_code else None,
        statuses=statuses,
        max_start_lag_days=max_start_lag_days,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run-id", default=None, help="指定 screen_run；默认取最新成功 run")
    parser.add_argument("--strategy", default=None, help="筛选最新 run 时限定 screen_runs.strategy")
    parser.add_argument("--period", default=None, help="筛选最新 run 时限定报告期")
    parser.add_argument("--anchor-date", default=None, help="覆盖起算日期，默认 screen_run.started_at")
    parser.add_argument("--windows", type=int, nargs="*", default=list(DEFAULT_WINDOWS))
    parser.add_argument("--statuses", nargs="*", default=list(DEFAULT_STATUSES),
                        help="参与回测的 candidate status，默认 hit watch")
    parser.add_argument("--benchmark-code", default=None,
                        help="可选基准代码；需已存在 pe_pb_history")
    parser.add_argument("--max-start-lag-days", type=int, default=10)
    parser.add_argument(
        "--output-dir",
        default=None,
        help="输出目录，默认 data/exports/backtests/<run_id>",
    )
    args = parser.parse_args()
    try:
        windows = list(normalize_windows(args.windows))
    except ValueError as e:
        logger.error(f"✗ {e}")
        return 1
    if args.max_start_lag_days < 0:
        logger.error("✗ --max-start-lag-days must be >= 0")
        return 1

    with DuckDBStore() as store:
        run_id = _resolve_run_id(
            store, run_id=args.run_id, strategy=args.strategy, period=args.period,
        )
        if not run_id:
            logger.error("✗ 找不到可回测的 screen_run")
            return 1
        anchor_date = _load_anchor_date(store, run_id, args.anchor_date)
        if anchor_date is None:
            logger.error(f"✗ run_id={run_id} 缺少 started_at，无法确定起算日")
            return 1
        results = run_backtest(
            store=store,
            run_id=run_id,
            anchor_date=anchor_date,
            windows=windows,
            statuses=args.statuses,
            benchmark_code=args.benchmark_code,
            max_start_lag_days=args.max_start_lag_days,
        )
        saved = store.save_backtest_results(results) if not results.empty else 0

    out_dir = Path(args.output_dir) if args.output_dir else (
        config.EXPORTS_DIR / "backtests" / run_id
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    results.to_csv(out_dir / "forward_returns.csv", index=False, encoding="utf-8-sig")
    summary = summarize_backtest(results)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _write_markdown(out_dir / "summary.md", run_id=run_id, summary=summary)

    logger.info(f"✓ backtest run_id={run_id} rows={len(results)} saved={saved}")
    logger.info(f"  output: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
