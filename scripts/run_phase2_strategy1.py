"""Phase 2：策略一（消费股反转）筛选 pipeline。

流程：
1. 从 DuckDB 加载候选股票池（含行业 + PE 快照）
2. 应用质量/流动性过滤器（剔除 ST / 低市值 / 极端 PE）
3. 行业粗筛：申万一级 食品饮料 / 家用电器 / 美容护理 / 商贸零售
4. 对每只候选股：
   - 拉历史 PE，算 5 年分位
   - 拉财务摘要，算扣非 TTM 同比增速
5. 应用阈值（分位 ≤ 30%、增速 ≥ 30%）
6. 输出 target_pool.csv
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.collectors import AkShareSource
from src.storage import DuckDBStore
from src.strategies import apply_quality_filter
from src.strategies.consumer_reversal import (
    StrategyConfig,
    run_consumer_reversal,
)


def main() -> int:
    print("=" * 70)
    print("Phase 2: 策略一（消费股反转）筛选")
    print("=" * 70)

    source = AkShareSource()
    store = DuckDBStore()

    try:
        # 1. 加载候选池
        print("\n[1/4] 从 DuckDB 加载候选股票池...")
        candidates = store.load_stock_industry()
        if candidates.empty:
            print("  ✗ 候选池为空，请先运行 bootstrap.py")
            return 1
        print(f"  ✓ 共 {len(candidates)} 只候选股票")

        # 2. 质量过滤
        print("\n[2/4] 质量过滤（ST / 市值 < 30 亿 / 极端 PE）...")
        filtered = apply_quality_filter(candidates)
        print(f"  ✓ 过滤后剩 {len(filtered)} 只")

        # 3. 行业粗筛 + 4. 策略一逐股评估
        print("\n[3/4] 跑策略一（行业粗筛 + PE 分位 + 扣非 TTM 增速）...")
        print(
            "  目标行业: 食品饮料 / 家用电器 / 美容护理 / 商贸零售"
        )
        print("  阈值: PE 分位 ≤ 30%, 扣非 TTM 同比 ≥ 30%, 5 年历史样本 ≥ 100 天")

        # 看下行业粗筛后还剩多少
        from src.strategies.consumer_reversal import TARGET_INDUSTRIES

        industry_filtered = filtered[filtered["sw_first"].isin(TARGET_INDUSTRIES)]
        print(f"  行业粗筛后剩 {len(industry_filtered)} 只（{industry_filtered['sw_first'].value_counts().to_dict()}）")

        config = StrategyConfig()
        result = run_consumer_reversal(source, filtered, config, show_progress=True)

        # 4. 输出
        print(f"\n[4/4] 输出结果...")
        print(f"  ✓ 策略一命中: {len(result)} 只")

        if not result.empty:
            # 重排列
            cols = [
                "code", "name", "sw_first", "report_date",
                "pe_ttm_current", "pe_percentile", "pe_min", "pe_median", "pe_max",
                "pe_sample_count", "deducted_yoy_growth", "revenue", "gross_margin",
            ]
            result = result[[c for c in cols if c in result.columns]]

            # 加策略标签 + 时间戳
            result.insert(0, "strategy", "消费反转")
            result["screened_at"] = pd.Timestamp.now()

            # 输出 CSV
            export_dir = PROJECT_ROOT / "data" / "exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            csv_path = export_dir / "target_pool.csv"
            result.to_csv(csv_path, index=False, encoding="utf-8-sig")
            print(f"  ✓ 已保存: {csv_path}")

            # 控制台预览
            print("\n  前 20 名命中:")
            preview_cols = [
                "code", "name", "sw_first", "pe_ttm_current",
                "pe_percentile", "deducted_yoy_growth",
            ]
            print(result[preview_cols].head(20).to_string(index=False))

            # 同时写 Markdown 报告
            md_path = export_dir / "strategy1_result.md"
            _write_md_report(md_path, result, len(candidates), len(filtered), len(industry_filtered))
            print(f"  ✓ 已保存: {md_path}")

        return 0

    except Exception as e:
        print(f"\n✗ 失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        store.close()


def _write_md_report(
    md_path: Path,
    result: pd.DataFrame,
    n_candidates: int,
    n_filtered: int,
    n_industry_filtered: int,
) -> None:
    lines = [
        "# 策略一（消费股反转）筛选结果",
        "",
        f"- **筛选时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **候选池**: {n_candidates} 只",
        f"- **质量过滤后**: {n_filtered} 只",
        f"- **行业粗筛后（食品饮料/家电/美容护理/商贸零售）**: {n_industry_filtered} 只",
        f"- **最终命中**: {len(result)} 只",
        "",
        "## 筛选条件",
        "",
        "| 维度 | 条件 |",
        "|------|------|",
        "| 行业 | 申万一级: 食品饮料 / 家用电器 / 美容护理 / 商贸零售 |",
        "| 估值 | 当前 PE-TTM 处于近 5 年 ≤ 30% 分位 |",
        "| 业绩 | 最近一期财报扣非净利润同比增速 ≥ 30% |",
        "| 质量 | 剔除 ST / 退市 |",
        "| 流动性 | 总市值 ≥ 30 亿元 |",
        "",
        "## 完整命中清单",
        "",
        "| 代码 | 名称 | 行业 | 报告期 | PE | PE分位 | 扣非同比 | 营收(亿) | 毛利率 |",
        "|------|------|------|--------|-----|-------|---------|---------|--------|",
    ]
    for _, r in result.iterrows():
        revenue_yi = r["revenue"] / 1e8 if pd.notna(r["revenue"]) else None
        gross_margin = r["gross_margin"]
        revenue_str = f"{revenue_yi:.1f}" if revenue_yi is not None else "-"
        gross_str = f"{gross_margin:.1f}%" if pd.notna(gross_margin) else "-"
        lines.append(
            f"| {r['code']} | {r['name']} | {r['sw_first']} | "
            f"{r['report_date']} | {r['pe_ttm_current']:.1f} | "
            f"{r['pe_percentile']:.1f}% | {r['deducted_yoy_growth'] * 100:+.1f}% | "
            f"{revenue_str} | {gross_str} |"
        )
    lines += [
        "",
        "## 下一步",
        "",
        "1. 人工抽查命中股票，确认逻辑合理性",
        "2. 对感兴趣的标的，调用 `python scripts/run_phase0_poc.py` 拉年报附注数据",
        "3. 后续 Phase 3+ 将加入策略二（医药）/ 策略三（出海）的筛选",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
