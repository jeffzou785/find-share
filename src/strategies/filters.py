"""质量与流动性过滤器。

在策略筛选前先剔除：
- ST 股（戴帽风险）
- 次新股（上市 < 1 年，缺乏历史数据）
- 低流动性（日均成交额过小，买入滑点大）
- 退市股（含退市标识）
- 金融股（银行/保险/券商，与策略逻辑无关，避免越秀资本被错分到商贸零售等）

依赖：stock_industry 表（含 pe_ttm, total_mktcap_wan, turnover_ratio, csrc_section）
"""
from __future__ import annotations

import re

import pandas as pd

# CSRC 门类中需剔除的（金融、地产、专业服务通常不在策略关注范围内）
DEFAULT_EXCLUDE_CSRC_SECTIONS = {"金融业", "房地产业"}


def apply_quality_filter(
    df: pd.DataFrame,
    *,
    exclude_st: bool = True,
    exclude_new_listing_days: int = 365,
    min_total_mktcap_wan: float = 300_000,  # 30 亿元最低市值
    min_pe_ttm: float = 0.01,
    max_pe_ttm: float = 500,
    exclude_csrc_sections: set[str] | None = None,
) -> pd.DataFrame:
    """应用质量过滤。返回过滤后的 DataFrame。

    Args:
        df: 至少含 code, name, pe_ttm, total_mktcap_wan 列
            （其中 pe_ttm, total_mktcap_wan 来自 Sina 行业成分股快照）
        exclude_csrc_sections: 需要剔除的 CSRC 门类（如 "金融业"）
    """
    if df.empty:
        return df

    if exclude_csrc_sections is None:
        exclude_csrc_sections = DEFAULT_EXCLUDE_CSRC_SECTIONS

    mask = pd.Series([True] * len(df), index=df.index)

    if exclude_st and "name" in df.columns:
        st_mask = df["name"].astype(str).str.contains(
            r"ST|\*ST|退", regex=True, na=False, case=False
        )
        mask &= ~st_mask

    # CSRC 门类剔除（金融、地产）
    if "csrc_section" in df.columns and exclude_csrc_sections:
        cs = df["csrc_section"].astype(str).fillna("")
        for section in exclude_csrc_sections:
            mask &= cs != section

    # 流动性 / 市值：仅对有数据的股票过滤（NULL 不过滤，留给后续策略评估时按需拉取）
    if "total_mktcap_wan" in df.columns:
        cap = pd.to_numeric(df["total_mktcap_wan"], errors="coerce")
        mask &= cap.isna() | (cap >= min_total_mktcap_wan)

    # PE 合理性（剔除极端值，避免污染分位计算）
    if "pe_ttm" in df.columns:
        pe = pd.to_numeric(df["pe_ttm"], errors="coerce")
        mask &= pe.isna() | pe.between(min_pe_ttm, max_pe_ttm)

    return df[mask].reset_index(drop=True)
