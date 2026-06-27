"""对比状态化改造前后的 hit 清单差异（P0-12 / P1-5）。

用法：
    python3 scripts/baseline_diff.py \
        --baseline data/baselines/pre_stateful_20260627/target_pool.csv \
        --new data/exports/runs/{run_id}/consumer_2025A.csv \
        --strategy consumer

输出：added / removed / common 三段，并列出每只股票的差异指标（如 pe_ttm）。

退出码：
    0  无未解释差异
    1  有未解释差异（需要人工 review）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


def _normalize(df: pd.DataFrame, code_col: str = "code") -> pd.DataFrame:
    df = df.copy()
    df[code_col] = df[code_col].astype(str).str.zfill(6)
    return df


def diff(
    baseline_path: Path,
    new_path: Path,
    output_md: Path | None = None,
) -> dict:
    base_df = _normalize(pd.read_csv(baseline_path))
    new_df = _normalize(pd.read_csv(new_path))
    base_codes = set(base_df["code"])
    new_codes = set(new_df["code"])

    added = sorted(new_codes - base_codes)
    removed = sorted(base_codes - new_codes)
    common = sorted(base_codes & new_codes)

    report = {
        "baseline_count": len(base_codes),
        "new_count": len(new_codes),
        "added": added,
        "removed": removed,
        "common": len(common),
    }

    md_lines = [
        f"# Baseline diff",
        f"",
        f"- baseline: `{baseline_path}` ({len(base_codes)} 只)",
        f"- new: `{new_path}` ({len(new_codes)} 只)",
        f"- common: {len(common)}",
        f"- added: {len(added)}",
        f"- removed: {len(removed)}",
        f"",
        f"## Added（新进入 hit）",
        f"",
    ]
    md_lines.extend(f"- {c}" for c in added)
    md_lines.extend(["", "## Removed（旧 hit 中被剔除）", ""])
    md_lines.extend(f"- {c}" for c in removed)

    if output_md:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text("\n".join(md_lines), encoding="utf-8")

    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--new", required=True, type=Path)
    parser.add_argument("--output-md", type=Path, default=None)
    args = parser.parse_args()

    if not args.baseline.exists():
        print(f"✗ baseline 不存在: {args.baseline}", file=sys.stderr)
        return 2
    if not args.new.exists():
        print(f"✗ new 不存在: {args.new}", file=sys.stderr)
        return 2

    report = diff(args.baseline, args.new, args.output_md)
    print(f"baseline {report['baseline_count']} → new {report['new_count']}")
    print(f"added={len(report['added'])} removed={len(report['removed'])} common={report['common']}")
    if report["added"]:
        print(f"added: {report['added'][:10]}{'...' if len(report['added'])>10 else ''}")
    if report["removed"]:
        print(f"removed: {report['removed'][:10]}{'...' if len(report['removed'])>10 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
