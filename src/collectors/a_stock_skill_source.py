"""A股 skill 数据源适配器。

本模块把 `$a-stock-data` 里推荐的直连接口封装成项目的 DataSource 形状：

- 估值快照：腾讯财经（PE/PB/市值/收盘价），不走 AkShare。
- 财务摘要：新浪财报三表，经 `SinaFinancialSource` 规整后汇总到 financials 表口径。

注意：腾讯只提供当前估值快照，不提供 3/5/10 年 PE/PB 历史。这里不会伪造
历史序列；缺历史分位的股票仍应在策略层进入 data_missing/watch。
"""
from __future__ import annotations

import urllib.request
from typing import Optional

import pandas as pd

from .base import normalize_code
from .sina_impl import SinaFinancialSource


class AStockSkillSource:
    """基于 `$a-stock-data` 公开接口的 A 股数据源。"""

    def __init__(
        self,
        *,
        sina_source: Optional[SinaFinancialSource] = None,
        quote_timeout: int = 10,
    ):
        self.sina_source = sina_source or SinaFinancialSource()
        self.quote_timeout = quote_timeout

    # === 全市场类接口暂不在此实现：项目 bootstrap 已有 stock_industry 落库 ===
    def get_stock_list(self) -> pd.DataFrame:
        raise NotImplementedError("AStockSkillSource 不负责全市场股票列表，请使用 bootstrap")

    def get_sw_first_industry(self) -> pd.DataFrame:
        raise NotImplementedError("AStockSkillSource 不负责申万行业列表，请使用 bootstrap")

    def get_sw_second_industry(self) -> pd.DataFrame:
        raise NotImplementedError("AStockSkillSource 不负责申万行业列表，请使用 bootstrap")

    def get_stock_industry_mapping(self) -> pd.DataFrame:
        raise NotImplementedError("AStockSkillSource 不负责行业映射，请使用 bootstrap-industry")

    def get_disclosure_calendar(self, period: str = "2025年报") -> pd.DataFrame:
        raise NotImplementedError("AStockSkillSource 暂不负责披露日历")

    def get_pe_pb_history(self, code: str, years: int = 5) -> pd.DataFrame:
        """腾讯估值快照。

        返回单行 DataFrame，列名与 `pe_pb_history` 表兼容。它只是当前快照，
        不满足历史分位样本数要求。
        """
        quote = tencent_quote_one(code, timeout=self.quote_timeout)
        if not quote:
            return pd.DataFrame()
        return pd.DataFrame([
            {
                "date": pd.Timestamp.now().normalize(),
                "close": quote.get("price"),
                "pe_ttm": quote.get("pe_ttm"),
                "pe_static": quote.get("pe_static"),
                "pb": quote.get("pb"),
                "total_mktcap": _yi_to_yuan(quote.get("mcap_yi")),
                "float_mktcap": _yi_to_yuan(quote.get("float_mcap_yi")),
            }
        ])

    def get_financial_abstract(self, code: str) -> pd.DataFrame:
        """从新浪三表汇总 financials 表需要的核心字段。"""
        code = normalize_code(code)
        full = self.get_financials_full(code)
        return financials_full_to_abstract(full)

    def get_financials_full(self, code: str, num: int = 9) -> pd.DataFrame:
        """返回新浪三表长格式，默认覆盖 2024Q1 至 2026Q1 的 9 个报告期。"""
        return self.sina_source.get_all_statements(normalize_code(code), num=num)


def _yi_to_yuan(value: Optional[float]) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return float(value) * 100_000_000.0


def _safe_float(value) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def tencent_quote_one(code: str, *, timeout: int = 10) -> dict:
    """腾讯财经 A 股行情/估值快照。"""
    code = normalize_code(code)
    prefix = "sh" if code.startswith(("6", "9")) else ("bj" if code.startswith("8") else "sz")
    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("gbk", errors="ignore")

    if '"' not in text:
        return {}
    vals = text.split('"')[1].split("~")
    if len(vals) < 53:
        return {}
    return {
        "code": code,
        "name": vals[1],
        "price": _safe_float(vals[3]),
        "pe_ttm": _safe_float(vals[39]),
        "mcap_yi": _safe_float(vals[44]),
        "float_mcap_yi": _safe_float(vals[45]),
        "pb": _safe_float(vals[46]),
        "pe_static": _safe_float(vals[52]),
    }


def financials_full_to_abstract(full: pd.DataFrame) -> pd.DataFrame:
    """把 financials_full 长表汇总成策略使用的 financials 宽表。"""
    if full.empty:
        return pd.DataFrame()

    df = full.copy()
    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
    rows: list[dict] = []
    for report_date, grp in df.dropna(subset=["report_date"]).groupby("report_date"):
        values = _latest_values_by_item_en(grp)
        yoy_values = _latest_yoy_by_item_en(grp)
        revenue = _first_not_none(values, "revenue", "operating_revenue")
        operating_cost = _first_not_none(values, "operating_cost")
        gross_margin = _first_not_none(values, "gross_margin")
        if gross_margin is None and revenue and operating_cost is not None and revenue > 0:
            gross_margin = (revenue - operating_cost) / revenue * 100.0

        ocf_net = _first_not_none(values, "ocf_net")
        share_capital = _first_not_none(values, "share_capital")
        ocf_per_share = None
        if ocf_net is not None and share_capital is not None and share_capital > 0:
            ocf_per_share = ocf_net / share_capital

        rows.append({
            "report_date": report_date,
            "revenue": revenue,
            "accounts_receivable": _first_not_none(values, "accounts_receivable"),
            "inventory": _first_not_none(values, "inventory"),
            "selling_expense": _first_not_none(values, "selling_expense"),
            "net_profit": _first_not_none(values, "net_profit"),
            "net_profit_attr_parent": _first_not_none(values, "net_profit_attr_parent"),
            "deducted_net_profit": _first_not_none(values, "deducted_net_profit"),
            "gross_margin": gross_margin,
            "revenue_yoy": _first_not_none(yoy_values, "revenue", "operating_revenue"),
            "net_profit_yoy": _first_not_none(
                yoy_values, "net_profit_attr_parent", "net_profit"
            ),
            "roe": _first_not_none(values, "roe"),
            "ocf_per_share": ocf_per_share,
        })

    return pd.DataFrame(rows).sort_values("report_date").reset_index(drop=True)


def _latest_values_by_item_en(grp: pd.DataFrame) -> dict[str, float]:
    out: dict[str, float] = {}
    cleaned = grp[grp["item_en"].astype(str) != ""].copy()
    for _, row in cleaned.iterrows():
        item_en = str(row["item_en"])
        val = _safe_float(row.get("value"))
        if val is not None and item_en not in out:
            out[item_en] = val
    return out


def _latest_yoy_by_item_en(grp: pd.DataFrame) -> dict[str, float]:
    out: dict[str, float] = {}
    cleaned = grp[grp["item_en"].astype(str) != ""].copy()
    for _, row in cleaned.iterrows():
        item_en = str(row["item_en"])
        val = _safe_float(row.get("value_yoy"))
        if val is not None and item_en not in out:
            out[item_en] = val
    return out


def _first_not_none(values: dict[str, float], *keys: str) -> Optional[float]:
    for key in keys:
        if key in values and values[key] is not None:
            return values[key]
    return None


__all__ = [
    "AStockSkillSource",
    "financials_full_to_abstract",
    "tencent_quote_one",
]
