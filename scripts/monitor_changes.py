"""P2-3 动态监控：对比两次 screen_run 的 candidate_scores。

用法：

    # 对比最近两次同 strategy+period 的 run
    python3 scripts/monitor_changes.py --strategy overseas --period 2025A

    # 显式指定两次 run_id
    python3 scripts/monitor_changes.py --before-run <run_id_old> --after-run <run_id_new>

    # 输出 Markdown 报告
    python3 scripts/monitor_changes.py --strategy overseas --period 2025A \\
        --output data/exports/run_diff.md

    # 仅输出高信号 alert（适合 cron / 通知 sink）
    python3 scripts/monitor_changes.py --strategy overseas --period 2025A \\
        --alert --output data/exports/alerts.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import config
from src.screening.run_diff import (
    diff_latest_two_runs,
    diff_runs,
    filter_alertable_events,
    write_alert_report,
)
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
    parser.add_argument(
        "--alert", action="store_true",
        help="只输出高信号 alert（new_hit/dropped_hit/大幅指标变化），
              适合通知场景；不指定时输出完整 diff",
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

        if args.alert:
            events = filter_alertable_events(diff)
            md = write_alert_report(diff)
            # exit code：有 alert 时返回 2（供 cron 检测）；无 alert 返回 0
            exit_code = 2 if events else 0
        else:
            md = diff.to_markdown()
            exit_code = 0

        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(md, encoding="utf-8")
            print(f"✓ {'alert' if args.alert else 'diff'} 已输出: {out_path} ({len(events) if args.alert else len(diff.events)} events)")
        else:
            print(md)
        return exit_code
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
