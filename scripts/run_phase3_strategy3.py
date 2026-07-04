"""Phase 3 策略三：出海隐形冠军筛选 pipeline。

流程：
1. 从 DuckDB 加载候选股票池（含行业 + CSRC 门类）
2. 行业粗筛：机械设备 / 交运设备 / 基础化工
3. 质量过滤（剔除金融、ST、低市值）
4. 对每只股票：
   - 读 overseas_revenue 表（已入库的境外收入）
   - 拉财务摘要，算营收、海外占比
   - 拉 PE 历史，取最新 PE-TTM
5. 应用阈值：
   - 海外占比 ≥ 30%
   - PE-TTM < 25
   - 海外同比 ≥ 40%（可选，需要 2 年数据）
6. 输出 target_pool_overseas.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.collectors import AStockSkillSource, DataSource, LocalCachedSource
from src.storage import DuckDBStore
from src.strategies import apply_quality_filter
from src.strategies.overseas_champion import (
    StrategyConfig,
    run_overseas_champion,
    TARGET_INDUSTRIES,
)


def main() -> int:
    print("=" * 70)
    print("Phase 3: 策略三（出海隐形冠军）筛选")
    print("=" * 70)

    store = DuckDBStore()
    # P1.5-1b：透明走 LocalCachedSource，先读本地，缺失才 fallback 到 $a-stock-data 直连源
    source = LocalCachedSource(store=store, upstream=AStockSkillSource())

    try:
        # 1. 加载候选池
        print("\n[1/4] 加载候选股票池...")
        candidates = store.load_stock_industry()
        if candidates.empty:
            print("  ✗ 候选池为空")
            return 1
        print(f"  ✓ {len(candidates)} 只候选股票")

        # 2. 质量过滤
        print("\n[2/4] 质量过滤（剔除金融/地产/ST）...")
        filtered = apply_quality_filter(candidates)
        print(f"  ✓ 过滤后剩 {len(filtered)} 只")

        # 3. 行业粗筛
        industry_filtered = filtered[filtered["sw_first"].isin(TARGET_INDUSTRIES)]
        print(
            f"\n[3/4] 行业粗筛（{', '.join(TARGET_INDUSTRIES[:3])}...）: "
            f"{len(industry_filtered)} 只"
        )

        # 4. 策略三评估
        print("\n[4/4] 跑策略三（境外占比 + PE-TTM < 25）...")
        config = StrategyConfig(require_overseas_yoy=False)
        result = run_overseas_champion(source, store, filtered, config, show_progress=True)

        print(f"\n  ✓ 策略三命中: {len(result)} 只")

        if not result.empty:
            cols = [
                "code", "name", "sw_first", "report_date",
                "overseas_revenue_yi", "revenue_yi", "overseas_ratio",
                "overseas_yoy", "overseas_data_year",
                "pe_ttm_current", "pe_percentile",
                # 扩展条件字段（cashflow + leverage 默认开启）
                "ocf_net_yi", "net_profit_yi", "ocf_to_profit",
                "total_liabilities_yi", "total_assets_yi", "debt_ratio",
                # 一致预期（默认关闭，需先批量拉研报）
                "eps_current", "eps_forecast_y1", "eps_forecast_y2",
                "eps_y1_growth", "eps_y2_growth",
            ]
            result = result[[c for c in cols if c in result.columns]]
            result.insert(0, "strategy", "出海隐形冠军")
            result["screened_at"] = pd.Timestamp.now()

            export_dir = PROJECT_ROOT / "data" / "exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            csv_path = export_dir / "target_pool_overseas.csv"
            result.to_csv(csv_path, index=False, encoding="utf-8-sig")
            print(f"  ✓ 已保存: {csv_path}")

            print("\n  命中清单:")
            preview_cols = [
                "code", "name", "sw_first", "overseas_revenue_yi",
                "overseas_ratio", "pe_ttm_current",
            ]
            with pd.option_context("display.width", 200, "display.max_columns", 10):
                print(result[preview_cols].to_string(index=False))

            # 检查未覆盖的潜在候选（行业+PE 合理但缺年报数据）
            print("\n  扩展池（行业+PE 候选，待下载年报解析）:")
            extension = _find_extension_candidates(source, filtered, result)
            if not extension.empty:
                ext_path = export_dir / "overseas_extension_candidates.csv"
                extension.to_csv(ext_path, index=False, encoding="utf-8-sig")
                print(f"    ✓ {len(extension)} 只候选 → {ext_path}")
                print(extension.head(20).to_string(index=False))

            # Markdown 报告
            md_path = export_dir / "strategy3_result.md"
            _write_md_report(md_path, result, len(candidates), len(industry_filtered), extension if not extension.empty else pd.DataFrame())
            print(f"  ✓ 已保存: {md_path}")

        return 0

    except Exception as e:
        print(f"\n✗ 失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        store.close()


def _find_extension_candidates(
    source: DataSource,
    filtered: pd.DataFrame,
    already_hit: pd.DataFrame,
    top_n: int = 50,
) -> pd.DataFrame:
    """找出行业符合但年报未入库的潜在候选，按市值排序输出。

    这些股票值得后续下载年报、解析境外收入。
    """
    industry_pool = filtered[filtered["sw_first"].isin(TARGET_INDUSTRIES)]
    if industry_pool.empty:
        return pd.DataFrame()

    hit_codes = set(already_hit["code"]) if not already_hit.empty else set()
    extension = industry_pool[~industry_pool["code"].isin(hit_codes)].copy()

    # 拉 PE，保留 PE<25 的（按策略三阈值预筛）
    rows = []
    for _, row in extension.iterrows():
        code = row["code"]
        try:
            pe_hist = source.get_pe_pb_history(code, years=1)
            if pe_hist.empty:
                continue
            latest_pe = pe_hist.sort_values("date").iloc[-1].get("pe_ttm")
            if pd.isna(latest_pe) or latest_pe <= 0 or latest_pe >= 25:
                continue
            rows.append(
                {
                    "code": code,
                    "name": row["name"],
                    "sw_first": row["sw_first"],
                    "em2016": row.get("em2016", ""),
                    "pe_ttm": float(latest_pe),
                }
            )
        except Exception:
            continue
        if len(rows) >= top_n:
            break

    return pd.DataFrame(rows).sort_values("pe_ttm").reset_index(drop=True) if rows else pd.DataFrame()


def _write_md_report(
    md_path: Path,
    result: pd.DataFrame,
    n_candidates: int,
    n_industry_filtered: int,
    extension: pd.DataFrame,
) -> None:
    import time

    lines = [
        "# 策略三（出海隐形冠军）筛选结果",
        "",
        f"- **筛选时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **候选池**: {n_candidates} 只",
        f"- **行业粗筛后（机械/汽车/化工）**: {n_industry_filtered} 只",
        f"- **已入库年报**: 仅覆盖已下载 PDF 解析的股票",
        f"- **最终命中**: {len(result)} 只",
        "",
        "## 筛选条件",
        "",
        "| 维度 | 条件 |",
        "|------|------|",
        "| 行业 | EM2016 一级: 机械设备 / 交运设备(汽车) / 基础化工 |",
        "| 海外业务 | 年报境外收入占总营收 ≥ 30% |",
        "| 海外增速 | 境外收入同比 ≥ 40%（可选，需 2 年数据）|",
        "| 估值 | PE-TTM < 25 |",
        "| 现金流质量 | 经营性现金流净额 / 净利润 ≥ 0.7（默认开启）|",
        "| 资产负债率 | 总负债 / 总资产 < 60%（默认开启）|",
        "| 一致预期增速 | 东财研报 EPS Y1/Y2 增速 ≥ 15%（默认关闭，需先批量拉研报）|",
        "| 质量 | 剔除 ST / 金融 / 地产 |",
        "",
        "## 命中清单",
        "",
        "| 代码 | 名称 | 行业 | 境外收入(亿) | 总营收(亿) | 占比 | 同比 | PE | 现金流/净利 | 负债率 |",
        "|------|------|------|------------|-----------|------|------|-----|------------|--------|",
    ]
    for _, r in result.iterrows():
        def _fmt(v, fmt: str, scale: float = 1.0, suffix: str = "") -> str:
            """None / NaN 安全的格式化；缺失返回 'N/A'。"""
            if v is None or not pd.notna(v):
                return "N/A"
            try:
                return format(v * scale, fmt) + suffix
            except (TypeError, ValueError):
                return "N/A"
        yoy = _fmt(r.get("overseas_yoy"), "+.1f%", scale=100)
        ocf_ratio = _fmt(r.get("ocf_to_profit"), ".2f")
        debt = _fmt(r.get("debt_ratio"), ".1f%", scale=100, suffix="%")
        overseas_rev = _fmt(r.get("overseas_revenue_yi"), ".1f")
        total_rev = _fmt(r.get("revenue_yi"), ".1f")
        ratio = _fmt(r.get("overseas_ratio"), ".1f%", scale=100, suffix="%")
        pe = _fmt(r.get("pe_ttm_current"), ".1f")
        lines.append(
            f"| {r['code']} | {r['name']} | {r.get('sw_first', '')} | "
            f"{overseas_rev} | {total_rev} | {ratio} | {yoy} | {pe} | "
            f"{ocf_ratio} | {debt} |"
        )

    if not extension.empty:
        lines += [
            "",
            "## 扩展池候选（PE<25 但年报未入库，待按需下载）",
            "",
            "| 代码 | 名称 | 行业 | EM2016 | PE |",
            "|------|------|------|--------|-----|",
        ]
        for _, r in extension.iterrows():
            lines.append(
                f"| {r['code']} | {r['name']} | {r['sw_first']} | "
                f"{r['em2016']} | {r['pe_ttm']:.1f} |"
            )

    lines += [
        "",
        "## 下一步",
        "",
        "1. 对扩展池中感兴趣的标的，运行 `python scripts/download_annual_reports.py <code1> <code2>` 下载年报",
        "2. 再跑 `python scripts/import_overseas_revenue.py` 入库",
        "3. 重新执行本脚本，候选会进入命中清单",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
