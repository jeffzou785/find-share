"""P1.5-1：批量预热 PE/PB 估值快照 + 财务摘要到本地 DuckDB。

跑过此脚本后，`run_after_disclosure.py` / `run_phase2_strategy1.py` /
`run_phase3_strategy3.py` 在 `--use-local-cache` 模式下从 DuckDB 直接读取，
默认不再实时调 AkShare，避免：
- 财报季外部接口数据被修订导致同一报告期跑两次结果不一致。
- 30 只候选股 × 多接口实时拉的耗时和反爬风险。

注意：默认财务上游是 `$a-stock-data` 口径的 `AStockSkillSource`。腾讯只提供当前
PE/PB 快照；策略一需要历史估值分位时，用 `--valuation-source akshare` 显式拉
东财历史估值序列。

用法：
    # 默认全候选池预热
    python3 scripts/refresh_financials_and_valuation.py

    # 只跑指定股票
    python3 scripts/refresh_financials_and_valuation.py --codes 600031 601058

    # 限制前 30 只（先小样本验证）
    python3 scripts/refresh_financials_and_valuation.py --limit 30

    # 只跑指定行业（按 sw_first 过滤）
    python3 scripts/refresh_financials_and_valuation.py --industries 机械设备 食品饮料

    # 强制覆盖本地已有数据（默认跳过已有本地数据的 code）
    python3 scripts/refresh_financials_and_valuation.py --force

    # 策略一补历史 PE/PB：财务仍走 $a-stock-data，估值走 AkShare 历史序列
    python3 scripts/refresh_financials_and_valuation.py --valuation-source akshare \
        --industries 食品饮料 家用电器 美容护理 商贸零售 纺织服饰 社会服务 轻工制造
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from tqdm import tqdm

from src.collectors import AStockSkillSource, AkShareSource, DataSource
from src.storage import DuckDBStore


def _has_sufficient_pe_history(
    store: DuckDBStore, code: str, min_pe_history_rows: int
) -> bool:
    try:
        pe = store.load_pe_pb_history(code)
        return len(pe) >= min_pe_history_rows
    except Exception:
        return False


def _has_financials(store: DuckDBStore, code: str) -> bool:
    try:
        fin = store.load_financials(code)
        return not fin.empty
    except Exception:
        return False


def _should_skip_code(
    store: DuckDBStore,
    code: str,
    force: bool,
    min_pe_history_rows: int = 1,
) -> bool:
    """force=False 时，只有估值样本和财务摘要都达标才跳过。"""
    if force:
        return False
    return (
        _has_sufficient_pe_history(store, code, min_pe_history_rows)
        and _has_financials(store, code)
    )


def _refresh_one(
    store: DuckDBStore,
    valuation_source: DataSource,
    financial_source: DataSource,
    code: str,
    min_pe_history_rows: int = 1,
) -> dict[str, str]:
    """刷新单只股票，并显式标记策略所需历史 PE 样本是否已达标。"""
    out = {"pe": "missing", "fin": "missing"}
    try:
        df = valuation_source.get_pe_pb_history(code, years=10)
        if not df.empty:
            store.save_pe_pb_history(code, df)
            # 接口返回可能含同日重复记录，写入表时会按 (code, date) upsert。
            # 策略读取的是持久化后的历史，因此这里也必须用同一口径判定达标。
            persisted_rows = len(store.load_pe_pb_history(code))
            status = (
                "ok" if persisted_rows >= min_pe_history_rows else "insufficient"
            )
            out["pe"] = f"{status}({persisted_rows})"
    except Exception as e:
        out["pe"] = f"error:{type(e).__name__}"
    try:
        df = financial_source.get_financial_abstract(code)
        if not df.empty:
            store.save_financials(code, df)
            out["fin"] = f"ok({len(df)})"
    except Exception as e:
        out["fin"] = f"error:{type(e).__name__}"
    return out


def _build_valuation_source(name: str, financial_source: AStockSkillSource) -> DataSource:
    if name == "a-stock":
        return financial_source
    if name == "akshare":
        return AkShareSource()
    raise ValueError(f"unknown valuation source: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--codes", nargs="*", default=None,
                        help="手动指定股票代码（默认全候选池）")
    parser.add_argument("--limit", type=int, default=None,
                        help="处理上限")
    parser.add_argument("--industries", nargs="*", default=None,
                        help="按 sw_first 过滤候选池")
    parser.add_argument("--force", action="store_true",
                        help="强制覆盖本地已有数据（默认跳过）")
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="关闭进度条，适合日志收集或非交互批量任务",
    )
    parser.add_argument(
        "--valuation-source",
        choices=["a-stock", "akshare"],
        default="a-stock",
        help="PE/PB 来源：a-stock=当前快照；akshare=东财历史序列（策略一建议）",
    )
    parser.add_argument(
        "--min-pe-history-rows",
        type=int,
        default=None,
        help="跳过刷新所需的最少 PE/PB 样本数；默认 a-stock=1，akshare=100",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("P1.5-1：刷新财务/估值数据到本地 DuckDB")
    print("=" * 70)

    store = DuckDBStore()
    financial_source = AStockSkillSource()
    try:
        valuation_source = _build_valuation_source(
            args.valuation_source, financial_source
        )
    except ModuleNotFoundError as e:
        print(f"✗ 初始化估值源失败: {e}")
        store.close()
        return 1
    min_pe_history_rows = (
        args.min_pe_history_rows
        if args.min_pe_history_rows is not None
        else (100 if args.valuation_source == "akshare" else 1)
    )

    try:
        candidates = store.load_stock_industry()
        if candidates.empty:
            print("✗ 候选池为空")
            return 1

        if args.codes:
            wanted = {str(c).zfill(6) for c in args.codes}
            candidates = candidates[
                candidates["code"].astype(str).str.zfill(6).isin(wanted)
            ]
        if args.industries:
            wanted_inds = set(args.industries)
            candidates = candidates[candidates["sw_first"].isin(wanted_inds)]
        if args.limit:
            candidates = candidates.head(args.limit)

        if candidates.empty:
            print("✗ 筛选后无候选股")
            return 1

        print(
            f"  候选 {len(candidates)} 只  force={args.force}  "
            f"valuation_source={args.valuation_source}  "
            f"min_pe_history_rows={min_pe_history_rows}"
        )

        counts = {"ok_pe": 0, "insufficient_pe": 0, "ok_fin": 0, "skip": 0,
                  "missing_pe": 0, "missing_fin": 0,
                  "error_pe": 0, "error_fin": 0}
        iterator = tqdm(
            candidates["code"].astype(str).str.zfill(6).tolist(),
            desc="刷新",
            ncols=80,
            disable=args.no_progress,
        )
        t0 = time.time()
        for code in iterator:
            if _should_skip_code(
                store, code, args.force,
                min_pe_history_rows=min_pe_history_rows,
            ):
                counts["skip"] += 1
                continue
            r = _refresh_one(
                store,
                valuation_source,
                financial_source,
                code,
                min_pe_history_rows=min_pe_history_rows,
            )
            if r["pe"].startswith("ok"):
                counts["ok_pe"] += 1
            elif r["pe"].startswith("insufficient"):
                counts["insufficient_pe"] += 1
            elif r["pe"].startswith("missing"):
                counts["missing_pe"] += 1
            else:
                counts["error_pe"] += 1
            if r["fin"].startswith("ok"):
                counts["ok_fin"] += 1
            elif r["fin"].startswith("missing"):
                counts["missing_fin"] += 1
            else:
                counts["error_fin"] += 1

        elapsed = time.time() - t0
        print()
        print(f"✓ 完成  耗时 {elapsed:.0f}s")
        print(f"  pe_pb_history: ok={counts['ok_pe']}  insufficient={counts['insufficient_pe']}  "
              f"missing={counts['missing_pe']}  "
              f"error={counts['error_pe']}  skip={counts['skip']}")
        print(f"  financials:    ok={counts['ok_fin']}  missing={counts['missing_fin']}  "
              f"error={counts['error_fin']}")

        # 验证：表行数
        pe_rows = store.conn.execute("SELECT COUNT(*) FROM pe_pb_history").fetchone()[0]
        fin_rows = store.conn.execute("SELECT COUNT(*) FROM financials").fetchone()[0]
        pe_codes = store.conn.execute(
            "SELECT COUNT(DISTINCT code) FROM pe_pb_history"
        ).fetchone()[0]
        fin_codes = store.conn.execute(
            "SELECT COUNT(DISTINCT code) FROM financials"
        ).fetchone()[0]
        print(f"  本地表：pe_pb_history={pe_rows} 行 / {pe_codes} 只；"
              f"financials={fin_rows} 行 / {fin_codes} 只")
        return 0

    except Exception as e:
        print(f"\n✗ 失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
