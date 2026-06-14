"""AkShare 数据源实现。

业务代码只依赖 DataSource 接口（base.py），本模块提供 AkShare 实现。
所有方法都通过 @akshare_call 装饰器，自动获得：
- 重试（指数退避）
- 本地缓存（pickle，24 小时）
- force_refresh=True 可跳过缓存
"""
from __future__ import annotations

import akshare as ak
import pandas as pd

from ..config import config
from ._retry import AkShareTransientError, akshare_call
from .base import DataSource, normalize_code, with_market_prefix
from .industry_mapping import SINA_SKIP_LABELS, sina_to_sw_first


class AkShareSource:
    """AkShare 实现。所有方法签名遵循 DataSource 协议。"""

    @akshare_call
    def get_stock_list(self) -> pd.DataFrame:
        df = ak.stock_info_a_code_name()
        if df is None or len(df) == 0:
            raise AkShareTransientError("stock_info_a_code_name 返回空")
        df = df.rename(columns={"code": "code", "name": "name"})
        df["code"] = df["code"].astype(str).str.zfill(6)
        return df[["code", "name"]]

    @akshare_call
    def get_sw_first_industry(self) -> pd.DataFrame:
        df = ak.sw_index_first_info()
        if df is None or len(df) == 0:
            raise AkShareTransientError("sw_index_first_info 返回空")
        return df.rename(
            columns={"行业代码": "industry_code", "行业名称": "industry_name"}
        )[["industry_code", "industry_name", "成份个数", "TTM(滚动)市盈率", "市净率"]]

    @akshare_call
    def get_sw_second_industry(self) -> pd.DataFrame:
        df = ak.sw_index_second_info()
        if df is None or len(df) == 0:
            raise AkShareTransientError("sw_index_second_info 返回空")
        return df.rename(
            columns={
                "行业代码": "industry_code",
                "行业名称": "industry_name",
                "上级行业": "parent_industry_name",
            }
        )[["industry_code", "industry_name", "parent_industry_name"]]

    def get_stock_industry_mapping(self) -> pd.DataFrame:
        """全市场股票 → 申万一级行业映射（通过 Sina 行业）。

        返回列: code, name, sina_industry(新浪行业名), sw_first(申万一级名)
        """
        sectors = self._get_sina_sectors()
        if sectors is None or len(sectors) == 0:
            raise AkShareTransientError("新浪行业列表返回空")

        # 跳过"次新股"和"其它行业"
        sectors = sectors[~sectors["label"].isin(SINA_SKIP_LABELS)]

        all_constituents: list[pd.DataFrame] = []
        for label, sina_name in zip(sectors["label"], sectors["板块"]):
            try:
                cons = self._get_sina_constituents(label, sina_name)
                if cons is not None and len(cons) > 0:
                    all_constituents.append(cons)
            except Exception:
                continue

        if not all_constituents:
            raise AkShareTransientError("所有新浪行业成分股都拉取失败")

        merged = pd.concat(all_constituents, ignore_index=True)
        # 去重：同一股票可能出现在多个行业（如其他行业 + 真实行业），保留第一个非 *行业
        merged = merged.sort_values("sw_first", key=lambda s: ~s.str.startswith("*"))
        merged = merged.drop_duplicates(subset=["code"], keep="first")
        return merged.reset_index(drop=True)

    @akshare_call
    def _get_sina_sectors(self) -> pd.DataFrame:
        return ak.stock_sector_spot(indicator="新浪行业")

    @akshare_call
    def _get_sina_constituents(self, label: str, sina_name: str) -> pd.DataFrame:
        df = ak.stock_sector_detail(sector=label)
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df = df.rename(columns={"code": "code", "name": "name"})
        df["code"] = df["code"].astype(str).str.zfill(6)
        df["sina_industry"] = sina_name
        df["sw_first"] = sina_to_sw_first(label, sina_name)
        # 顺便带出 PE/PB/市值/换手率，省一次接口调用
        df = df.rename(
            columns={
                "per": "pe_ttm",
                "pb": "pb",
                "mktcap": "total_mktcap_wan",
                "nmc": "float_mktcap_wan",
                "turnoverratio": "turnover_ratio",
            }
        )
        cols = [
            "code", "name", "sina_industry", "sw_first",
            "pe_ttm", "pb", "total_mktcap_wan", "float_mktcap_wan", "turnover_ratio",
        ]
        return df[[c for c in cols if c in df.columns]]

    @akshare_call
    def get_pe_pb_history(self, code: str, years: int = 5) -> pd.DataFrame:
        code = normalize_code(code)
        df = ak.stock_value_em(symbol=code)
        if df is None or len(df) == 0:
            raise AkShareTransientError(f"stock_value_em({code}) 返回空")
        df = df.rename(
            columns={
                "数据日期": "date",
                "PE(TTM)": "pe_ttm",
                "PE(静)": "pe_static",
                "市净率": "pb",
                "总市值": "total_mktcap",
                "流通市值": "float_mktcap",
                "当日收盘价": "close",
            }
        )
        df["date"] = pd.to_datetime(df["date"])
        # 截止最近 years 年
        cutoff = df["date"].max() - pd.DateOffset(years=years)
        df = df[df["date"] >= cutoff].sort_values("date").reset_index(drop=True)
        cols = ["date", "close", "pe_ttm", "pe_static", "pb", "total_mktcap", "float_mktcap"]
        return df[[c for c in cols if c in df.columns]]

    @akshare_call
    def get_financial_abstract(self, code: str) -> pd.DataFrame:
        """东财财务摘要，行=指标，列=报告期。本方法转成长格式返回。"""
        code = normalize_code(code)
        df = ak.stock_financial_abstract(symbol=code)
        if df is None or len(df) == 0:
            raise AkShareTransientError(f"stock_financial_abstract({code}) 返回空")

        # 长 → 宽：行=指标，列=报告期
        # 选项 列是分类（如 "按报告期" / "按年度"），指标列是字段名
        # 报告期列名是 yyyymmdd 格式
        metric_col = "指标"
        period_cols = [c for c in df.columns if str(c).isdigit() and len(str(c)) == 8]

        rows = []
        # 关键指标 → 统一字段名
        metric_map = {
            "营业总收入": "revenue",
            "归母净利润": "net_profit_attr_parent",
            "净利润": "net_profit",
            "扣非净利润": "deducted_net_profit",
            "毛利率": "gross_margin",
            "营业总收入增长率": "revenue_yoy",
            "归属母公司净利润增长率": "net_profit_yoy",
            "净资产收益率": "roe",
            "每股经营现金流": "ocf_per_share",
        }
        for _, row in df.iterrows():
            metric_cn = str(row[metric_col])
            if metric_cn not in metric_map:
                continue
            field = metric_map[metric_cn]
            for p in period_cols:
                val = row[p]
                try:
                    val = float(val) if pd.notna(val) else None
                except (ValueError, TypeError):
                    val = None
                # gross_margin 是百分比单位（如 89.76 表示 89.76%），保留原值
                rows.append(
                    {
                        "report_date": pd.to_datetime(p, format="%Y%m%d"),
                        "metric": field,
                        "value": val,
                    }
                )

        if not rows:
            raise AkShareTransientError(f"{code} 财务摘要未匹配到任何关键指标")

        long_df = pd.DataFrame(rows)
        # 去重：同一 (report_date, metric) 可能出现多次，取第一个非空
        long_df = long_df.dropna(subset=["value"])
        long_df = long_df.drop_duplicates(subset=["report_date", "metric"], keep="first")
        wide = long_df.pivot(index="report_date", columns="metric", values="value").reset_index()
        wide.columns.name = None
        return wide.sort_values("report_date").reset_index(drop=True)

    @akshare_call
    def get_disclosure_calendar(self, period: str = "2025年报") -> pd.DataFrame:
        df = ak.stock_report_disclosure(market="沪深京", period=period)
        if df is None or len(df) == 0:
            raise AkShareTransientError(f"stock_report_disclosure({period}) 返回空")
        df = df.rename(
            columns={
                "股票代码": "code",
                "股票简称": "name",
                "首次预约": "first_schedule",
                "初次变更": "first_change",
                "二次变更": "second_change",
                "三次变更": "third_change",
                "实际披露": "actual_date",
            }
        )
        df["code"] = df["code"].astype(str).str.zfill(6)
        for col in ["first_schedule", "actual_date"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        return df[["code", "name", "first_schedule", "actual_date"]]
