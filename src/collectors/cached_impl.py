"""P1.5-1：本地 DuckDB 缓存的数据源装饰器。

读顺序：先查 DuckDB `pe_pb_history` / `financials` 表；缺失才调被装饰的 upstream
（通常是 AkShareSource），并把结果回写本地。

与 `@akshare_call` 的 24h pickle 缓存区别：
- pickle 缓存：临时文件，进程退出后失效，按过期时间重新拉。
- DuckDB 缓存：永久持久化，可跨进程共享，支持 `refresh_financials_and_valuation.py`
  主动批量预热；策略代码读本地命中后完全不走网络，保证同一报告期多次跑结果一致。

不实现 `get_stock_list / get_stock_industry_mapping` 等全市场类接口：这类数据
量小、变化慢，bootstrap 阶段一次性写入 `stocks / stock_industry` 表即可。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ..storage import DuckDBStore
from .base import DataSource


class LocalCachedSource(DataSource):
    """装饰另一个 DataSource，给 `get_pe_pb_history` / `get_financial_abstract`
    增加本地 DuckDB 缓存（read-through + write-back）。

    用法：
        upstream = AkShareSource()
        source = LocalCachedSource(store=store, upstream=upstream)
        # 业务代码无感

    缓存命中时不访问 upstream；缓存缺失时拉 upstream 并回写。
    """

    def __init__(
        self,
        store: DuckDBStore,
        upstream: DataSource,
        *,
        writeback: bool = True,
    ):
        self.store = store
        self.upstream = upstream
        self.writeback = writeback

    # === 全市场类接口：透传 upstream（不缓存） ===
    def get_stock_list(self) -> pd.DataFrame:
        return self.upstream.get_stock_list()

    def get_sw_first_industry(self) -> pd.DataFrame:
        return self.upstream.get_sw_first_industry()

    def get_sw_second_industry(self) -> pd.DataFrame:
        return self.upstream.get_sw_second_industry()

    def get_stock_industry_mapping(self) -> pd.DataFrame:
        return self.upstream.get_stock_industry_mapping()

    def get_disclosure_calendar(self, period: str = "2025年报") -> pd.DataFrame:
        return self.upstream.get_disclosure_calendar(period)

    # === 单股缓存接口 ===
    def get_pe_pb_history(self, code: str, years: int = 5) -> pd.DataFrame:
        local = self._safe_load_local(
            lambda: self.store.load_pe_pb_history(code)
        )
        if not local.empty:
            df = local.copy()
            df["date"] = pd.to_datetime(df["date"])
            cutoff = df["date"].max() - pd.DateOffset(years=years)
            filtered = df[df["date"] >= cutoff].sort_values("date").reset_index(drop=True)
            if not filtered.empty:
                return filtered

        # 本地空 → 拉 upstream → 回写
        df = self.upstream.get_pe_pb_history(code, years=years)
        if self.writeback and not df.empty:
            try:
                self.store.save_pe_pb_history(code, df)
            except Exception:
                pass
        return df

    def get_financial_abstract(self, code: str) -> pd.DataFrame:
        local = self._safe_load_local(
            lambda: self.store.load_financials(code)
        )
        if not local.empty:
            return local.copy()

        df = self.upstream.get_financial_abstract(code)
        if self.writeback and not df.empty:
            try:
                self.store.save_financials(code, df)
            except Exception:
                pass
        return df

    def _safe_load_local(self, loader) -> pd.DataFrame:
        """包一层异常：本地表损坏或缺失时不影响 fallback 路径。"""
        try:
            return loader()
        except Exception:
            return pd.DataFrame()
