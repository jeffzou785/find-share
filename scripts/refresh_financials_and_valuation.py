"""P1.5-1：批量预热 PE/PB 历史 + 财务摘要到本地 DuckDB。

跑过此脚本后，`run_after_disclosure.py` / `run_phase2_strategy1.py` /
`run_phase3_strategy3.py` 在 `--use-local-cache` 模式下从 DuckDB 直接读取，
不再实时调 AkShare，避免：
- 财报季 AkShare 数据被修订导致同一报告期跑两次结果不一致。
- 30 只候选股 × 多接口实时拉的耗时和反爬风险。

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

from src.collectors import AkShareSource
from src.storage import DuckDBStore


def _should_skip_code(store: DuckDBStore, code: str, force: bool) -> bool:
    """force=False 时，本地已有 pe_pb_history 或 financials 的 code 跳过。"""
    if force:
        return False
    try:
        pe = store.load_pe_pb_history(code)
        if not pe.empty:
            return True
    except Exception:
        pass
    try:
        fin = store.load_financials(code)
        if not fin.empty:
            return True
    except Exception:
        pass
    return False


def _refresh_one(
    store: DuckDBStore, source: AkShareSource, code: str
) -> dict[str, str]:
    """刷新单只股票。返回 {"pe": "ok|missing|error", "fin": "ok|missing|error"}。"""
    out = {"pe": "missing", "fin": "missing"}
    try:
        df = source.get_pe_pb_history(code, years=10)
        if not df.empty:
            store.save_pe_pb_history(code, df)
            out["pe"] = f"ok({len(df)})"
    except Exception as e:
        out["pe"] = f"error:{type(e).__name__}"
    try:
        df = source.get_financial_abstract(code)
        if not df.empty:
            store.save_financials(code, df)
            out["fin"] = f"ok({len(df)})"
    except Exception as e:
        out["fin"] = f"error:{type(e).__name__}"
    return out


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
    args = parser.parse_args()

    print("=" * 70)
    print("P1.5-1：刷新财务/估值数据到本地 DuckDB")
    print("=" * 70)

    store = DuckDBStore()
    source = AkShareSource()

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

        print(f"  候选 {len(candidates)} 只  force={args.force}")

        counts = {"ok_pe": 0, "ok_fin": 0, "skip": 0,
                  "missing_pe": 0, "missing_fin": 0,
                  "error_pe": 0, "error_fin": 0}
        iterator = tqdm(candidates["code"].astype(str).str.zfill(6).tolist(),
                        desc="刷新", ncols=80)
        t0 = time.time()
        for code in iterator:
            if _should_skip_code(store, code, args.force):
                counts["skip"] += 1
                continue
            r = _refresh_one(store, source, code)
            if r["pe"].startswith("ok"):
                counts["ok_pe"] += 1
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
        print(f"  pe_pb_history: ok={counts['ok_pe']}  missing={counts['missing_pe']}  "
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
