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
            "current": 当前真实值（按 df 末行，可能为负/NaN）,
            "current_valid": 当前值是否可用于分位比较（>0 且 <1000）,
            "min": 最小值,
            "max": 最大值,
            "median": 中位数,
            "percentile": 当前值的百分位（0-100）,
            "sample_count": 样本数,
        }

    当前值为负/异常时：返回 current=原值、current_valid=False、percentile=None，
    让上层用 current_valid 判定是否走 pe_ttm_invalid 分支，避免历史上用过滤后
    的最后一条正值（可能已数月之久）错算分位。
    """
    if history.empty or value_col not in history.columns:
        return _empty_result()

    df = history[["date", value_col]].copy()
    df["date"] = pd.to_datetime(df["date"])
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)

    # 取末行原始值作为 "current"，不再用过滤后的 window.iloc[-1]
    last_row = df.iloc[-1] if not df.empty else None
    raw_current = (
        None if last_row is None or pd.isna(last_row[value_col])
        else float(last_row[value_col])
    )
    current_valid = (
        raw_current is not None and 0 < raw_current < 1000
    )

    # 过滤异常值（PE/PB 应为正且合理），仅用于历史样本统计
    series = df[value_col].dropna()
    series = series[(series > 0) & (series < 1000)]

    if len(series) < 30:
        return _empty_result(
            sample_count=len(series),
            current=raw_current,
            current_valid=current_valid,
        )

    cutoff = df["date"].max() - pd.DateOffset(years=years)
    mask = (df["date"] >= cutoff) & df[value_col].notna() & (df[value_col] > 0) & (df[value_col] < 1000)
    window = df.loc[mask, value_col]

    if len(window) < 30:
        return _empty_result(
            sample_count=len(window),
            current=raw_current,
            current_valid=current_valid,
        )

    if not current_valid:
        # 当前真实 PE 无效（亏损或极端值）→ 不算分位
        return {
            "current": raw_current,
            "current_valid": False,
            "min": float(window.min()),
            "max": float(window.max()),
            "median": float(window.median()),
            "percentile": None,
            "sample_count": int(len(window)),
        }

    return {
        "current": raw_current,
        "current_valid": True,
        "min": float(window.min()),
        "max": float(window.max()),
        "median": float(window.median()),
        "percentile": float((window <= raw_current).sum() / len(window) * 100),
        "sample_count": int(len(window)),
    }


def _empty_result(
    sample_count: int = 0,
    current: float | None = None,
    current_valid: bool | None = None,
) -> dict:
    return {
        "current": current,
        "current_valid": current_valid if current_valid is not None else False,
        "min": None,
        "max": None,
        "median": None,
        "percentile": None,
        "sample_count": sample_count,
    }
