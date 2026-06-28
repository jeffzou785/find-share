"""P2-3 动态监控：对比两次 screen_run 的 candidate_scores。

用法：

    # 对比最近两次同 strategy+period 的 run
    python3 scripts/monitor_changes.py --strategy overseas --period 2025A

    # 显式指定两次 run_id
    python3 scripts/monitor_changes.py --before-run <run_id_old> --after-run <run_id_new>

    # 输出 Markdown 报告
    python3 scripts/monitor_changes.py --strategy overseas --period 2025A \\
        --output data/exports/run_diff.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import config
from src.screening.run_diff import diff_latest_two_runs, diff_runs
from src.storage import DuckDBStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--strategy", default=None,
        help="按 strategy 过滤（与 --period 配合找最近两次 run）",
    )
    parser.add_argument(
        "--period", default=None,
        help="按 period 过滤（如 2025A / 2025Q1）",
    )
    parser.add_argument(
        "--before-run", default=None,
        help="显式指定旧 run_id（优先于 --strategy/--period）",
    )
    parser.add_argument(
        "--after-run", default=None,
        help="显式指定新 run_id",
    )
    parser.add_argument(
        "--output", default=None,
        help="Markdown 报告输出路径（默认打印到 stdout）",
    )
    args = parser.parse_args()

    store = DuckDBStore()
    try:
        if args.before_run and args.after_run:
            diff = diff_runs(store, args.before_run, args.after_run)
        else:
            if not args.strategy or not args.period:
                print(
                    "✗ 需要 --before-run + --after-run，"
                    "或 --strategy + --period"
                )
                return 1
            diff = diff_latest_two_runs(
                store, strategy=args.strategy, period=args.period
            )
            if diff is None:
                print(
                    f"✗ {args.strategy}/{args.period} 不足两次 run，"
                    "无可对比"
                )
                return 1

        md = diff.to_markdown()
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(md, encoding="utf-8")
            print(f"✓ diff 已输出: {out_path}")
        else:
            print(md)
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
