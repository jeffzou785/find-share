"""数据源抽象层。

业务代码（指标、策略、监控）只依赖 DataSource 接口，不直接调 AkShare/Tushare。
升级数据源时只需新增一个实现类 + 改 config。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class DataSource(Protocol):
    """统一数据源接口。所有方法返回规整后的 DataFrame。"""

    # === 基础数据 ===
    def get_stock_list(self) -> pd.DataFrame:
        """返回全市场股票列表。列: code(6位), name"""
        ...

    def get_sw_first_industry(self) -> pd.DataFrame:
        """申万一级行业列表。列: industry_code, industry_name"""
        ...

    def get_sw_second_industry(self) -> pd.DataFrame:
        """申万二级行业列表。列: industry_code, industry_name, parent_industry_name"""
        ...

    def get_stock_industry_mapping(self) -> pd.DataFrame:
        """全市场股票 → 申万行业映射。
        列: code, name, sw_first(一级行业名), sw_second(二级行业名)
        """
        ...

    # === 估值数据 ===
    def get_pe_pb_history(self, code: str, years: int = 5) -> pd.DataFrame:
        """单只股票的历史 PE/PB 时间序列。
        列: date, pe_ttm, pe_static, pb, market_cap, close
        """
        ...

    # === 财务数据 ===
    def get_financial_abstract(self, code: str) -> pd.DataFrame:
        """单只股票的财务摘要（含扣非净利润、毛利率、增长率）。
        列: report_date, revenue, net_profit, deducted_net_profit, gross_margin,
            revenue_yoy, deducted_net_profit_yoy, ...
        """
        ...

    # === 披露日历 ===
    def get_disclosure_calendar(self, period: str = "2025年报") -> pd.DataFrame:
        """财报披露日历。列: code, name, first_schedule, last_change, actual_date"""
        ...


def normalize_code(code: str) -> str:
    """规整股票代码为无前缀的 6 位字符串。"""
    code = str(code).strip().upper().replace("SH", "").replace("SZ", "").replace("BJ", "")
    return code.zfill(6) if code.isdigit() else code


def with_market_prefix(code: str) -> str:
    """为 6 位代码加交易所前缀（如 600519 → SH600519）。

    规则：
    - 6 开头 → SH
    - 688 开头 → SH (科创板)
    - 8/4 开头 → BJ (北交所)
    - 其他 → SZ
    """
    code = normalize_code(code)
    if code.startswith("6"):
        return f"SH{code}"
    if code.startswith("8") or code.startswith("4"):
        return f"BJ{code}"
    return f"SZ{code}"
