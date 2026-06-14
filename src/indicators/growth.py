"""财务增长率计算。

AkShare 的 stock_financial_abstract 给的是累计报告期数据（如 2026Q1 = 1-3 月）。
要算"扣非净利润同比增速"需要：
1. 用最近 4 个季度的累计值算 TTM
2. 同比 = 当年 TTM / 去年同期 TTM - 1
"""
from __future__ import annotations

import pandas as pd


def compute_deducted_ttm(financials: pd.DataFrame) -> pd.DataFrame:
    """从财务摘要计算单股的扣非净利润 TTM（最近 4 个季度的滚动累计）。

    financials 至少含 report_date, deducted_net_profit。
    返回增加列: deducted_ttm, report_period_type
    """
    df = financials.copy()
    if df.empty or "deducted_net_profit" not in df.columns:
        return df

    df["report_date"] = pd.to_datetime(df["report_date"])
    df = df.sort_values("report_date").reset_index(drop=True)
    df["year"] = df["report_date"].dt.year
    df["quarter"] = df["report_date"].dt.month.map({3: 1, 6: 2, 9: 3, 12: 4})

    # Q1=Q1, Q2=H1, Q3=前三季, Q4=全年
    # TTM 算法：当前累计 - 去年同期累计 + 去年全年
    # 简化：用当前累计 + (去年全年 - 去年同期累计)
    df["deducted_ttm"] = None

    for i, row in df.iterrows():
        if pd.isna(row["deducted_net_profit"]):
            continue
        cur_year = row["year"]
        cur_q = row["quarter"]
        cur_cum = row["deducted_net_profit"]

        if cur_q == 4:
            ttm = cur_cum  # 全年本身就是 TTM
        else:
            # 找去年全年
            last_year_full = df.loc[
                (df["year"] == cur_year - 1) & (df["quarter"] == 4),
                "deducted_net_profit",
            ]
            if last_year_full.empty:
                continue
            last_year_full_val = last_year_full.iloc[0]

            # 找去年同季度累计
            last_year_same_q = df.loc[
                (df["year"] == cur_year - 1) & (df["quarter"] == cur_q),
                "deducted_net_profit",
            ]
            if last_year_same_q.empty:
                continue
            last_year_same_q_val = last_year_same_q.iloc[0]

            ttm = cur_cum + (last_year_full_val - last_year_same_q_val)

        df.at[i, "deducted_ttm"] = ttm

    return df


def compute_yoy_growth(
    financials: pd.DataFrame,
    metric: str = "deducted_ttm",
) -> pd.DataFrame:
    """计算指定指标的同比增速。

    返回增加列: {metric}_yoy_growth（小数，0.3 = 30%）
    """
    df = financials.copy()
    if df.empty or metric not in df.columns:
        return df

    df["report_date"] = pd.to_datetime(df["report_date"])
    df = df.sort_values("report_date").reset_index(drop=True)
    df["year"] = df["report_date"].dt.year
    df["quarter"] = df["report_date"].dt.month.map({3: 1, 6: 2, 9: 3, 12: 4})

    growth_col = f"{metric}_yoy_growth"
    df[growth_col] = None

    for i, row in df.iterrows():
        cur_val = row[metric]
        if pd.isna(cur_val) or cur_val == 0:
            continue
        cur_year = row["year"]
        cur_q = row["quarter"]

        prev = df.loc[
            (df["year"] == cur_year - 1) & (df["quarter"] == cur_q),
            metric,
        ]
        if prev.empty:
            continue
        prev_val = prev.iloc[0]
        if pd.isna(prev_val) or prev_val == 0:
            continue
        df.at[i, growth_col] = (cur_val - prev_val) / abs(prev_val)

    return df


def get_latest_periods(financials: pd.DataFrame, n: int = 2) -> pd.DataFrame:
    """获取最近 n 个报告期。"""
    df = financials.copy()
    if df.empty:
        return df
    df["report_date"] = pd.to_datetime(df["report_date"])
    return df.sort_values("report_date").tail(n).reset_index(drop=True)
