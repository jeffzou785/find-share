"""策略一：消费股反转。

逻辑：
- 行业：申万/EM2016 一级 食品饮料 / 家用电器(或家电) / 美容护理 / 商贸零售
- 估值：当前 PE-TTM 处于近 5 年 30% 分位以下
- 业绩：最近一期财报扣非净利润同比增速 > 30%

输入：股票池 + DataSource（拉估值历史和财务摘要）
输出：DataFrame[code, name, sw_first, pe_ttm, pe_percentile, deducted_yoy_growth, ...]
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd
from tqdm import tqdm

from ..collectors.base import DataSource
from ..indicators.growth import compute_deducted_ttm, compute_yoy_growth
from ..indicators.valuation import compute_pe_pb_percentile


# 策略一目标行业（同时兼容申万 + EM2016 行业名）
TARGET_INDUSTRIES = [
    "食品饮料",
    "家用电器", "家电",  # 申万 vs EM2016
    "美容护理",
    "商贸零售", "商业百货",  # 兼容多种命名
]

# 默认阈值
DEFAULT_PE_PERCENTILE_MAX = 30.0
DEFAULT_DEDUCTED_YOY_MIN = 0.30  # 30%


@dataclass
class StrategyConfig:
    pe_percentile_max: float = DEFAULT_PE_PERCENTILE_MAX
    deducted_yoy_min: float = DEFAULT_DEDUCTED_YOY_MIN
    history_years: int = 5
    min_history_samples: int = 100  # PE 历史样本至少这么多


def run_consumer_reversal(
    source: DataSource,
    candidates: pd.DataFrame,
    config: StrategyConfig | None = None,
    show_progress: bool = True,
) -> pd.DataFrame:
    """跑策略一筛选。

    Args:
        source: 数据源
        candidates: 候选股票池（至少含 code, name, sw_first）
        config: 策略参数
        show_progress: 是否显示进度条
    """
    config = config or StrategyConfig()

    if candidates.empty:
        return pd.DataFrame()

    # 行业粗筛
    pool = candidates[candidates["sw_first"].isin(TARGET_INDUSTRIES)].copy()
    if pool.empty:
        return pd.DataFrame()

    results = []
    iterator = zip(pool["code"], pool["name"], pool["sw_first"])
    if show_progress:
        iterator = tqdm(list(iterator), desc="策略一筛选", ncols=80)

    for code, name, sw_first in iterator:
        try:
            row = _evaluate_one(source, code, name, sw_first, config)
            if row:
                results.append(row)
        except Exception as e:
            # 静默失败（写日志的话可以加 logger）
            continue

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    # 应用阈值
    mask = (
        (df["pe_percentile"] <= config.pe_percentile_max)
        & (df["deducted_yoy_growth"] >= config.deducted_yoy_min)
    )
    return df[mask].sort_values("deducted_yoy_growth", ascending=False).reset_index(drop=True)


def _evaluate_one(
    source: DataSource,
    code: str,
    name: str,
    sw_first: str,
    config: StrategyConfig,
) -> dict | None:
    # 1. PE 历史分位
    try:
        pe_hist = source.get_pe_pb_history(code, years=config.history_years)
    except Exception:
        return None

    pe_stat = compute_pe_pb_percentile(pe_hist, "pe_ttm", config.history_years)
    if (
        pe_stat["percentile"] is None
        or pe_stat["sample_count"] < config.min_history_samples
    ):
        return None

    # 2. 扣非 TTM 增速
    try:
        fin = source.get_financial_abstract(code)
    except Exception:
        return None

    if fin.empty or "deducted_net_profit" not in fin.columns:
        return None

    fin_ttm = compute_deducted_ttm(fin)
    fin_growth = compute_yoy_growth(fin_ttm, "deducted_ttm")

    if fin_growth.empty:
        return None

    latest = fin_growth.sort_values("report_date").iloc[-1]
    yoy = latest.get("deducted_ttm_yoy_growth")
    if pd.isna(yoy):
        return None

    # 3. 取最新财报日期 + 营收/毛利率
    report_date = latest["report_date"]
    revenue = latest.get("revenue")
    gross_margin = latest.get("gross_margin")

    return {
        "code": code,
        "name": name,
        "sw_first": sw_first,
        "report_date": pd.to_datetime(report_date).date() if pd.notna(report_date) else None,
        "pe_ttm_current": pe_stat["current"],
        "pe_percentile": pe_stat["percentile"],
        "pe_min": pe_stat["min"],
        "pe_median": pe_stat["median"],
        "pe_max": pe_stat["max"],
        "pe_sample_count": pe_stat["sample_count"],
        "deducted_yoy_growth": float(yoy),
        "revenue": float(revenue) if pd.notna(revenue) else None,
        "gross_margin": float(gross_margin) if pd.notna(gross_margin) else None,
    }
