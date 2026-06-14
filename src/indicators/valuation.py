"""估值分位计算。

基于历史 PE/PB 时间序列，计算当前值在过去 N 年的百分位。
"""
from __future__ import annotations

import pandas as pd


def compute_pe_pb_percentile(
    history: pd.DataFrame,
    value_col: str = "pe_ttm",
    years: int = 5,
) -> dict:
    """计算单股的估值历史分位。

    Args:
        history: 至少含 date 和 value_col 两列
        value_col: pe_ttm / pe_static / pb
        years: 回看年数

    Returns:
        {
            "current": 当前值,
            "min": 最小值,
            "max": 最大值,
            "median": 中位数,
            "percentile": 当前值的百分位（0-100）,
            "sample_count": 样本数,
        }
    """
    if history.empty or value_col not in history.columns:
        return _empty_result()

    df = history[["date", value_col]].copy()
    df["date"] = pd.to_datetime(df["date"])
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")

    # 过滤异常值（PE/PB 应为正且合理）
    series = df[value_col].dropna()
    series = series[(series > 0) & (series < 1000)]  # 剔除负值和极端值

    if len(series) < 30:
        return _empty_result(sample_count=len(series))

    cutoff = df["date"].max() - pd.DateOffset(years=years)
    mask = (df["date"] >= cutoff) & df[value_col].notna() & (df[value_col] > 0) & (df[value_col] < 1000)
    window = df.loc[mask, value_col]

    if len(window) < 30:
        return _empty_result(sample_count=len(window))

    current = float(window.iloc[-1])
    return {
        "current": current,
        "min": float(window.min()),
        "max": float(window.max()),
        "median": float(window.median()),
        "percentile": float((window <= current).sum() / len(window) * 100),
        "sample_count": int(len(window)),
    }


def _empty_result(sample_count: int = 0) -> dict:
    return {
        "current": None,
        "min": None,
        "max": None,
        "median": None,
        "percentile": None,
        "sample_count": sample_count,
    }
