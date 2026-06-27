"""财报披露后自动筛选入口（P0-7 / P0-8 / P0-9 / P0-10）。

第一版只跑年报场景。用法：

    # 跑指定股票
    python3 scripts/run_after_disclosure.py --period 2025A --codes 600031 601058

    # 从披露表拉，限制 30 只
    python3 scripts/run_after_disclosure.py --period 2025A --from-disclosures --limit 30

    # 选择策略
    python3 scripts/run_after_disclosure.py --period 2025A --strategy consumer
    python3 scripts/run_after_disclosure.py --period 2025A --strategy overseas
    python3 scripts/run_after_disclosure.py --period 2025A --strategy all

    # 断点续跑
    python3 scripts/run_after_disclosure.py --period 2025A --resume

输出：data/exports/runs/{run_id}/
  - consumer_2025A.csv / overseas_2025A.csv
  - report.md
  - rejected_reasons.csv
  - data_missing.csv
  - errors.csv
"""
from __future__ import annotations

import argparse
import secrets
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.collectors import AkShareSource, LocalCachedSource
from src.config import config
from src.screening import ConfigSchema, ScreeningResult, Status, Thresholds
from src.screening.schemas import DataSources, RuntimeConfig
from src.storage import DuckDBStore
from src.strategies.consumer_reversal import (
    StrategyConfig as ConsumerConfig,
    evaluate_consumer_full,
)
from src.strategies.overseas_champion import (
    StrategyConfig as OverseasConfig,
    evaluate_overseas_full,
)


STRATEGIES = ("consumer", "overseas", "all")
REPORT_TYPES = {"A": "annual", "H": "half_year", "Q1": "q1", "Q3": "q3"}


def _gen_run_id(period: str) -> str:
    """格式：YYYYMMDD_HHMMSS_xxxx（xxxx 4 位随机）。"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{secrets.token_hex(2)}_{period}"


def _period_to_report_type(period: str) -> str:
    """2025A → annual；2025H → half_year；2025Q1 → q1；2025Q3 → q3。"""
    if not period or len(period) < 5:
        return "annual"
    suffix = period[4:].upper()
    return REPORT_TYPES.get(suffix, "annual")


def _print_disclosures_coverage(store: DuckDBStore, period: str) -> tuple[int, int, float]:
    """返回 (rows, actual_date_non_null, coverage_pct)。"""
    try:
        df = store.load_disclosures(period)
    except Exception:
        return 0, 0, 0.0
    if df.empty:
        print(f"  ⚠ disclosures 表 period={period} 为空")
        return 0, 0, 0.0
    rows = len(df)
    actual_non_null = (
        df["actual_date"].notna().sum() if "actual_date" in df.columns else 0
    )
    coverage = (actual_non_null / rows) * 100 if rows else 0.0
    print(
        f"  disclosures period={period} rows={rows} "
        f"actual_date_non_null={actual_non_null} coverage={coverage:.1f}%"
    )
    return rows, int(actual_non_null), coverage


def _resolve_codes(
    *,
    args: argparse.Namespace,
    store: DuckDBStore,
    period: str,
    candidates: pd.DataFrame,
) -> list[str]:
    if args.codes:
        return [str(c).zfill(6) for c in args.codes]
    if args.from_disclosures:
        rows, _, coverage = _print_disclosures_coverage(store, period)
        if rows == 0:
            print("  ⚠ disclosures 为空，回退到候选池（可改用 --codes 手动指定）")
        else:
            disc = store.load_disclosures(period)
            # 优先选 actual_date 不为空的（已披露）
            if "actual_date" in disc.columns:
                disc = disc[disc["actual_date"].notna()]
            if disc.empty:
                print("  ⚠ disclosures 中无已披露记录，回退到候选池")
            else:
                codes = disc["code"].astype(str).str.zfill(6).tolist()
                if args.limit:
                    codes = codes[: args.limit]
                return codes
    # 默认从候选池取
    codes = candidates["code"].astype(str).str.zfill(6).tolist()
    if args.limit:
        codes = codes[: args.limit]
    return codes


def _build_consumer_config(args) -> ConsumerConfig:
    return ConsumerConfig()


def _build_overseas_config(args) -> OverseasConfig:
    return OverseasConfig()


def _build_config_schema(
    strategy: str, period: str, args
) -> ConfigSchema:
    cfg = ConfigSchema(
        strategy=strategy,
        period=period,
        framework_version="0.1.0",
        thresholds=Thresholds(),
        data_sources=DataSources(),
        runtime=RuntimeConfig(
            max_workers=1,
            non_em_max_workers=args.non_em_max_workers,
            single_request_timeout=args.single_request_timeout,
            retry_times=args.retry_times,
            resume=args.resume,
        ),
    )
    return cfg


def _filter_candidates_by_codes(
    candidates: pd.DataFrame, codes: list[str]
) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame()
    wanted = {str(c).zfill(6) for c in codes}
    return candidates[candidates["code"].astype(str).str.zfill(6).isin(wanted)]


def _run_strategy(
    *,
    strategy: str,
    source: AkShareSource,
    store: DuckDBStore,
    candidates_subset: pd.DataFrame,
    run_id: str,
    period: str,
    skip_codes: set[str],
    enable_neglect_evidence: bool = False,
) -> list[ScreeningResult]:
    """跑单个策略；返回 ScreeningResult 列表（已处理 --resume 跳过）。"""
    if candidates_subset.empty:
        return []
    if strategy == "consumer":
        results = evaluate_consumer_full(
            source=source, candidates=candidates_subset,
            run_id=run_id, period=period, show_progress=True,
            config=_build_consumer_config(None),
        )
    elif strategy == "overseas":
        results = evaluate_overseas_full(
            source=source, store=store, candidates=candidates_subset,
            run_id=run_id, period=period, show_progress=True,
            config=_build_overseas_config(None),
            enable_neglect_evidence=enable_neglect_evidence,
        )
    else:
        raise ValueError(f"未知 strategy: {strategy}")
    if skip_codes:
        results = [r for r in results if r.code not in skip_codes]
    return results


def _save_results_to_store(
    store: DuckDBStore, run_id: str, results: list[ScreeningResult]
) -> dict[str, int]:
    if not results:
        return {}
    store.save_candidate_scores([r.to_row() for r in results])
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    return counts


def _export_run_outputs(
    *,
    run_id: str,
    period: str,
    consumer_results: list[ScreeningResult],
    overseas_results: list[ScreeningResult],
    candidates_total: int,
):
    """生成 data/exports/runs/{run_id}/ 下的 CSV 和 Markdown 报告。"""
    out_dir = config.EXPORTS_DIR / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    all_results = consumer_results + overseas_results

    # CSV：每个策略一份 hit 清单
    for strategy, results in (("consumer", consumer_results), ("overseas", overseas_results)):
        hits = [r for r in results if r.status == Status.HIT]
        if hits:
            df = pd.DataFrame([_result_to_csv_row(r) for r in hits])
            df.to_csv(out_dir / f"{strategy}_{period}.csv",
                      index=False, encoding="utf-8-sig")

    # rejected 原因分布
    rejected = [r for r in all_results if r.status == Status.REJECTED]
    if rejected:
        df = pd.DataFrame(
            [{"code": r.code, "strategy": r.strategy,
              "reject_reason": r.reject_reason} for r in rejected]
        )
        df.to_csv(out_dir / "rejected_reasons.csv",
                  index=False, encoding="utf-8-sig")

    # data_missing 清单
    missing = [r for r in all_results if r.status == Status.DATA_MISSING]
    if missing:
        df = pd.DataFrame(
            [{"code": r.code, "strategy": r.strategy,
              "data_missing_reason": r.data_missing_reason} for r in missing]
        )
        df.to_csv(out_dir / "data_missing.csv",
                  index=False, encoding="utf-8-sig")

    # 错误清单
    errors = [r for r in all_results if r.status == Status.ERROR]
    if errors:
        df = pd.DataFrame(
            [{"code": r.code, "strategy": r.strategy,
              "error": r.error} for r in errors]
        )
        df.to_csv(out_dir / "errors.csv",
                  index=False, encoding="utf-8-sig")

    # Markdown 报告
    _write_markdown_report(
        out_dir / "report.md",
        run_id=run_id, period=period,
        consumer_results=consumer_results,
        overseas_results=overseas_results,
        candidates_total=candidates_total,
    )
    return out_dir


def _result_to_csv_row(r: ScreeningResult) -> dict:
    """hit 清单的核心字段（用于人工 review）。"""
    m = r.metrics
    return {
        "code": r.code,
        "name": r.name,
        "strategy": r.strategy,
        "period": r.period,
        "hit_reason": r.hit_reason,
        "pe_ttm": m.valuation.pe_ttm,
        "pe_pct_5y": m.valuation.pe_pct_5y,
        "pb": m.valuation.pb,
        "pb_pct_5y": m.valuation.pb_pct_5y,
        "deducted_profit_yoy_ttm": m.growth.deducted_profit_yoy_ttm,
        "revenue_yoy": m.growth.revenue_yoy,
        "gross_margin": m.quality.gross_margin,
        "overseas_ratio": m.overseas.overseas_ratio,
        "overseas_yoy": m.overseas.overseas_yoy,
        "overseas_revenue_yi": m.overseas.overseas_revenue_yi,
        "debt_ratio": m.quality.debt_ratio,
        "ocf_to_net_profit": m.quality.ocf_to_net_profit,
        "reports_count_90d": m.catalyst.reports_count_90d,  # P1-2
    }


def _write_markdown_report(
    md_path: Path,
    *,
    run_id: str,
    period: str,
    consumer_results: list[ScreeningResult],
    overseas_results: list[ScreeningResult],
    candidates_total: int,
) -> None:
    def _count(rs: list[ScreeningResult], status: Status) -> int:
        return sum(1 for r in rs if r.status == status)

    def _status_summary(rs: list[ScreeningResult]) -> str:
        if not rs:
            return "未跑（strategy 未启用）"
        return (
            f"hit={_count(rs, Status.HIT)} "
            f"watch={_count(rs, Status.WATCH)} "
            f"rejected={_count(rs, Status.REJECTED)} "
            f"data_missing={_count(rs, Status.DATA_MISSING)} "
            f"error={_count(rs, Status.ERROR)}"
        )

    lines = [
        f"# Run {run_id}",
        "",
        f"- **period**: {period}",
        f"- **candidates_total**: {candidates_total}",
        f"- **generated_at**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 汇总",
        "",
        f"- 策略一（消费反转）: {_status_summary(consumer_results)}",
        f"- 策略三（出海隐形冠军）: {_status_summary(overseas_results)}",
        "",
    ]

    for name, rs in (("策略一（消费反转）", consumer_results),
                     ("策略三（出海隐形冠军）", overseas_results)):
        if not rs:
            continue
        lines += [
            f"## {name} 命中清单",
            "",
            "| 代码 | 名称 | PE | PE 分位 | 扣非同比 | 海外占比 | 营收同比 | 毛利率 | 研报90d | hit_reason |",
            "|------|------|----|--------|---------|---------|---------|--------|--------|-----------|",
        ]
        for r in [x for x in rs if x.status == Status.HIT]:
            m = r.metrics
            pe = f"{m.valuation.pe_ttm:.1f}" if m.valuation.pe_ttm else "-"
            pct = f"{m.valuation.pe_pct_5y:.1f}%" if m.valuation.pe_pct_5y else "-"
            yoy = (
                f"{m.growth.deducted_profit_yoy_ttm*100:+.1f}%"
                if m.growth.deducted_profit_yoy_ttm is not None else "-"
            )
            ratio = (
                f"{m.overseas.overseas_ratio*100:.1f}%"
                if m.overseas.overseas_ratio is not None else "-"
            )
            rev_yoy = (
                f"{m.growth.revenue_yoy*100:+.1f}%"
                if m.growth.revenue_yoy is not None else "-"
            )
            gm = (
                f"{m.quality.gross_margin*100:.1f}%"
                if m.quality.gross_margin is not None else "-"
            )
            reports = (
                str(m.catalyst.reports_count_90d)
                if m.catalyst.reports_count_90d is not None else "-"
            )
            lines.append(
                f"| {r.code} | {r.name or ''} | {pe} | {pct} | {yoy} | {ratio} "
                f"| {rev_yoy} | {gm} | {reports} | {r.hit_reason} |"
            )
        lines.append("")

        # watch 清单
        watched = [x for x in rs if x.status == Status.WATCH]
        if watched:
            lines += [
                f"### {name} Watch 清单",
                "",
                "| 代码 | 名称 | watch_reason | parse_warning |",
                "|------|------|-------------|---------------|",
            ]
            for r in watched:
                lines.append(
                    f"| {r.code} | {r.name or ''} | {r.watch_reason} | "
                    f"{r.metrics.overseas.parse_warning or ''} |"
                )
            lines.append("")

        # 剔除原因分布
        rejected = [x for x in rs if x.status == Status.REJECTED]
        if rejected:
            counts: dict[str, int] = {}
            for r in rejected:
                counts[r.reject_reason] = counts.get(r.reject_reason, 0) + 1
            lines += [
                f"### {name} 剔除原因分布",
                "",
                "| reject_reason | count |",
                "|---------------|-------|",
            ]
            for reason, n in sorted(counts.items(), key=lambda x: -x[1]):
                lines.append(f"| {reason} | {n} |")
            lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")


def _merge_fingerprints(fingerprints: dict[str, str]) -> str:
    """多策略时合并 fingerprint：按 key 排序拼接，保证 consumer/overseas 顺序无关。

    单策略时调用方应直接用该策略的 fingerprint，不走这里。
    """
    sorted_fps = sorted(fingerprints.items())
    return "|".join(f"{k}:{v}" for k, v in sorted_fps)


def _load_skip_codes_for_resume(
    store: DuckDBStore,
    period: str,
    strategy_arg: str,
    expected_fp: str,
) -> set[str]:
    """--resume 模式下找出最近一次同 strategy + fingerprint 的 run 里已成功的 code。

    strategy_arg 是用户传入的 --strategy 值（'all' / 'consumer' / 'overseas'），
    screen_runs.strategy 字段同此值。多策略时 expected_fp 用合并 fingerprint。
    """
    latest = store.conn.execute(
        "SELECT run_id, config_fingerprint FROM screen_runs "
        "WHERE strategy = ? AND period = ? "
        "  AND status IN ('success', 'partial_success') "
        "ORDER BY started_at DESC LIMIT 1",
        [strategy_arg, period],
    ).df()
    if latest.empty:
        return set()
    prev_fp = latest.iloc[0]["config_fingerprint"]
    if prev_fp != expected_fp:
        print(f"  ⚠ 最近 run 配置已变（fingerprint 不同），不跳过")
        return set()
    prev_run_id = latest.iloc[0]["run_id"]
    prev_rows = store.load_candidate_scores(prev_run_id)
    done = prev_rows[prev_rows["status"].isin(["hit", "watch", "rejected"])]
    return set(done["code"].astype(str).str.zfill(6).tolist())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--period", required=True, help="报告期，如 2025A")
    parser.add_argument("--codes", nargs="*", default=None,
                        help="手动指定股票代码")
    parser.add_argument("--from-disclosures", action="store_true",
                        help="从 disclosures 表读取已披露股票")
    parser.add_argument("--limit", type=int, default=None,
                        help="首批处理上限")
    parser.add_argument(
        "--strategy", choices=STRATEGIES, default="all",
        help="consumer / overseas / all",
    )
    parser.add_argument("--resume", action="store_true",
                        help="跳过最近同配置 run 里 hit/watch/rejected 的股票")
    parser.add_argument("--non-em-max-workers", type=int, default=2)
    parser.add_argument("--single-request-timeout", type=int, default=30)
    parser.add_argument("--retry-times", type=int, default=2)
    parser.add_argument(
        "--enable-neglect-evidence", action="store_true",
        help="P1.5-3：开启被忽视证据链（新闻/概念/热点），增加网络耗时",
    )
    args = parser.parse_args()

    period = args.period
    report_type = _period_to_report_type(period)
    strategies = (
        ["consumer", "overseas"] if args.strategy == "all" else [args.strategy]
    )

    # 每个 ConfigSchema 按 strategy 字段区分，fingerprint 也不同。
    # screen_runs 表的 config_fingerprint 字段：
    # - 单策略 (--strategy consumer): 该策略的 fingerprint
    # - 多策略 (--strategy all): 子策略 fingerprint 排序拼接（顺序无关）
    config_schemas = {
        s: _build_config_schema(s, period, args) for s in strategies
    }
    sub_fps = {s: config_schemas[s].fingerprint() for s in strategies}
    if args.strategy == "all":
        primary_fp = _merge_fingerprints(sub_fps)
    else:
        primary_fp = sub_fps[args.strategy]

    run_id = _gen_run_id(period)
    print(f"run_id = {run_id}")
    print(f"period = {period}  report_type = {report_type}")
    print(f"strategies = {strategies}  resume = {args.resume}")

    store = DuckDBStore()
    n_cleaned = store.cleanup_stale_screen_runs(max_age_hours=1)
    if n_cleaned:
        print(f"  ✓ 清理超时 running: {n_cleaned} 个")

    try:
        # 加载候选池
        candidates = store.load_stock_industry()
        if candidates.empty:
            print("✗ 候选池为空，请先运行 bootstrap_emweb_industry.py")
            return 1
        print(f"  ✓ 候选池 {len(candidates)} 只")

        # 决定 code 列表
        codes = _resolve_codes(
            args=args, store=store, period=period, candidates=candidates,
        )
        if not codes:
            print("✗ 无 code 可处理")
            return 1
        print(f"  ✓ 待处理 {len(codes)} 只")

        candidates_subset = _filter_candidates_by_codes(candidates, codes)
        print(f"  ✓ 匹配候选池 {len(candidates_subset)} 只")

        # 创建 screen_runs
        store.create_screen_run(
            run_id=run_id, strategy=args.strategy, period=period,
            report_type=report_type,
            config_json=config_schemas[strategies[0]].to_json(),
            config_fingerprint=primary_fp,
            input_count=len(candidates_subset),
        )

        # resume 跳过
        skip_codes: set[str] = set()
        if args.resume:
            skip_codes = _load_skip_codes_for_resume(
                store, period, args.strategy, primary_fp,
            )
            if skip_codes:
                print(f"  ✓ --resume 跳过 {len(skip_codes)} 只已完成")

        # P1.5-1：默认走 LocalCachedSource，先读 DuckDB，缺失才 fallback AkShare + 回写
        source = LocalCachedSource(store=store, upstream=AkShareSource())

        # 跑策略
        consumer_results: list[ScreeningResult] = []
        overseas_results: list[ScreeningResult] = []
        all_counts: dict[str, int] = {}

        if "consumer" in strategies:
            consumer_results = _run_strategy(
                strategy="consumer", source=source, store=store,
                candidates_subset=candidates_subset,
                run_id=run_id, period=period, skip_codes=skip_codes,
            )
            c = _save_results_to_store(store, run_id, consumer_results)
            for k, v in c.items():
                all_counts[k] = all_counts.get(k, 0) + v

        if "overseas" in strategies:
            overseas_results = _run_strategy(
                strategy="overseas", source=source, store=store,
                candidates_subset=candidates_subset,
                run_id=run_id, period=period, skip_codes=skip_codes,
                enable_neglect_evidence=args.enable_neglect_evidence,
            )
            c = _save_results_to_store(store, run_id, overseas_results)
            for k, v in c.items():
                all_counts[k] = all_counts.get(k, 0) + v

        # 输出
        out_dir = _export_run_outputs(
            run_id=run_id, period=period,
            consumer_results=consumer_results,
            overseas_results=overseas_results,
            candidates_total=len(candidates_subset),
        )

        # 状态判定
        has_error = all_counts.get("error", 0) > 0
        has_missing = all_counts.get("data_missing", 0) > 0
        if has_error:
            run_status = "partial_success"
        elif has_missing:
            run_status = "partial_success"
        else:
            run_status = "success"
        store.finish_screen_run(run_id, run_status, counts=all_counts)

        print()
        print(f"✓ run_id={run_id} status={run_status}")
        print(f"  counts: {all_counts}")
        print(f"  output: {out_dir}")
        return 0

    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            store.finish_screen_run(run_id, "failed", error=f"{type(e).__name__}: {e}")
        except Exception:
            pass
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
