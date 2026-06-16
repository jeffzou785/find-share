"""策略三：出海隐形冠军。

逻辑：
- 行业：EM2016/申万一级 机械设备 / 交运设备(汽车) / 基础化工
- 海外业务：2024 年报境外收入占比 > 30%
- 海外增速：境外收入同比 > 40%（需 2 个年度数据；无则跳过此条件）
- 估值：当前 PE-TTM < 25

数据依赖：
- stock_industry 表（行业映射）
- overseas_revenue 表（年报解析结果）
- pe_pb_history 表 + indicators.valuation 算 PE
- financials 表（拉营收算海外占比）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd
from tqdm import tqdm

from ..collectors.base import DataSource
from ..indicators.valuation import compute_pe_pb_percentile
from ..storage import DuckDBStore


# 策略三目标行业（EM2016 一级 + 申万兼容名）
TARGET_INDUSTRIES = [
    "机械设备",
    "交运设备",  # EM2016 用这个
    "汽车",  # 申万
    "基础化工",
    "化工",  # 兼容
]

DEFAULT_OVERSEAS_RATIO_MIN = 0.30  # 30%
DEFAULT_OVERSEAS_YOY_MIN = 0.40  # 40%
DEFAULT_PE_TTM_MAX = 25.0


@dataclass
class StrategyConfig:
    overseas_ratio_min: float = DEFAULT_OVERSEAS_RATIO_MIN
    overseas_ratio_max: float = 0.95  # 海外占比 >95% 视为解析异常（抓成总营收）
    overseas_yoy_min: float = DEFAULT_OVERSEAS_YOY_MIN
    pe_ttm_max: float = DEFAULT_PE_TTM_MAX
    require_overseas_yoy: bool = False  # 当前只入库 1 年数据时跳过增速校验
    sanity_check_yoy: bool = True  # 用同比做数据合理性过滤（|yoy|>5 或 <-80% 视为单位识别错）


def run_overseas_champion(
    source: DataSource,
    store: DuckDBStore,
    candidates: pd.DataFrame,
    config: StrategyConfig | None = None,
    show_progress: bool = True,
) -> pd.DataFrame:
    """跑策略三筛选。

    Args:
        source: 数据源
        store: DuckDB（读 overseas_revenue 表）
        candidates: 候选股票池（含 code, name, sw_first）
        config: 策略参数
    """
    config = config or StrategyConfig()

    if candidates.empty:
        return pd.DataFrame()

    # 1. 行业粗筛
    pool = candidates[candidates["sw_first"].isin(TARGET_INDUSTRIES)].copy()
    if pool.empty:
        return pd.DataFrame()

    # 2. 拉已入库的 overseas_revenue
    overseas_df = store.load_overseas_revenue()
    if overseas_df.empty:
        print("  ⚠ overseas_revenue 表为空，请先跑 scripts/import_overseas_revenue.py")
        return pd.DataFrame()

    # 把 overseas_revenue 转成"按股票 + 年份"的金额表
    overseas_df["revenue_yuan"] = (
        overseas_df["revenue"]
        * overseas_df["revenue_unit"].map(
            {"元": 1.0, "千元": 1_000.0, "万元": 10_000.0, "百万": 1_000_000.0, "亿元": 100_000_000.0}
        ).fillna(1.0)
    )
    # 合理性校验（沿用解析器逻辑）：超过 5e12 / 1e4
    overseas_df.loc[overseas_df["revenue_yuan"] > 5e12, "revenue_yuan"] = (
        overseas_df.loc[overseas_df["revenue_yuan"] > 5e12, "revenue_yuan"] / 1e4
    )

    # 海外收入表
    overseas_map = {}
    for _, row in overseas_df.iterrows():
        key = (row["stock_code"], int(row["report_year"]))
        overseas_map.setdefault(row["stock_code"], {})[int(row["report_year"])] = row["revenue_yuan"]

    print(
        f"  ✓ 已加载 {len(overseas_map)} 只股票的境外收入数据"
        f"（report_year 范围: {sorted(set(overseas_df['report_year']))}）"
    )

    # 3. 逐股评估
    results = []
    iterator = zip(pool["code"], pool["name"], pool["sw_first"])
    if show_progress:
        iterator = tqdm(list(iterator), desc="策略三筛选", ncols=80)

    for code, name, sw_first in iterator:
        try:
            row = _evaluate_one(source, store, code, name, sw_first, config, overseas_map)
            if row:
                results.append(row)
        except Exception:
            continue

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    # 应用阈值
    mask = (
        (df["overseas_ratio"] >= config.overseas_ratio_min)
        & (df["overseas_ratio"] < config.overseas_ratio_max)
        & (df["pe_ttm_current"] <= config.pe_ttm_max)
    )
    # 数据合理性过滤：同比异常（|yoy|>5 或 <-80%）视为单位识别错
    if config.sanity_check_yoy and "overseas_yoy" in df.columns:
        yoy = df["overseas_yoy"]
        anomaly = yoy.notna() & ((yoy.abs() > 5) | (yoy < -0.8))
        mask &= ~anomaly
    # 启用同比阈值（严格模式）
    if config.require_overseas_yoy and "overseas_yoy" in df.columns:
        mask &= df["overseas_yoy"].fillna(-1) >= config.overseas_yoy_min

    return df[mask].sort_values("overseas_ratio", ascending=False).reset_index(drop=True)


def _evaluate_one(
    source: DataSource,
    store: DuckDBStore,
    code: str,
    name: str,
    sw_first: str,
    config: StrategyConfig,
    overseas_map: dict,
) -> Optional[dict]:
    # 1. 检查是否有 overseas_revenue 数据
    if code not in overseas_map:
        return None  # 没拉过年报，跳过

    yearly = overseas_map[code]
    latest_year = max(yearly.keys())
    overseas_revenue = yearly[latest_year]
    if overseas_revenue <= 0:
        return None

    # 2. 计算海外收入同比
    overseas_yoy = None
    if latest_year - 1 in yearly:
        prev = yearly[latest_year - 1]
        if prev > 0:
            overseas_yoy = (overseas_revenue - prev) / prev

    # 3. 拉营收（算海外占比）—— 必须取与海外收入同年度的全年营收
    try:
        fin = source.get_financial_abstract(code)
    except Exception:
        return None

    if fin.empty:
        return None

    fin_sorted = fin.sort_values("report_date") if "report_date" in fin.columns else fin
    # 优先：取 overseas_revenue 同年的年报（如 2024 → report_date = 2024-12-31）
    target_date = pd.Timestamp(year=latest_year, month=12, day=31)
    annual_rows = fin_sorted[
        pd.to_datetime(fin_sorted["report_date"]) == target_date
    ]
    if annual_rows.empty:
        # 退路：取最新一期年报（报告期月份=12）
        fin_sorted_copy = fin_sorted.copy()
        fin_sorted_copy["report_date"] = pd.to_datetime(fin_sorted_copy["report_date"])
        annual_rows = fin_sorted_copy[fin_sorted_copy["report_date"].dt.month == 12]
    if annual_rows.empty:
        return None

    latest_fin = annual_rows.iloc[-1]
    revenue = latest_fin.get("revenue")
    if pd.isna(revenue) or revenue <= 0:
        return None

    overseas_ratio = overseas_revenue / revenue

    # 4. 拉 PE 历史
    try:
        pe_hist = source.get_pe_pb_history(code, years=5)
    except Exception:
        return None

    if pe_hist.empty:
        return None
    latest_pe_row = pe_hist.sort_values("date").iloc[-1]
    pe_ttm = latest_pe_row.get("pe_ttm")
    if pd.isna(pe_ttm) or pe_ttm <= 0:
        return None

    # 5. PE 历史分位（信息项，不强制阈值）
    pe_stat = compute_pe_pb_percentile(pe_hist, "pe_ttm", years=5)

    return {
        "code": code,
        "name": name,
        "sw_first": sw_first,
        "report_date": pd.to_datetime(latest_fin["report_date"]).date() if pd.notna(latest_fin.get("report_date")) else None,
        "overseas_revenue_yi": overseas_revenue / 1e8,  # 亿元
        "revenue_yi": revenue / 1e8,
        "overseas_ratio": overseas_ratio,
        "overseas_yoy": overseas_yoy,
        "overseas_data_year": latest_year,
        "pe_ttm_current": float(pe_ttm),
        "pe_percentile": pe_stat["percentile"],
    }
