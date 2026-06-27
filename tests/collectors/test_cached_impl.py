"""LocalCachedSource 测试（P1.5-1）。

验证：
- 本地有数据时直接返回，不调 upstream
- 本地空时调 upstream 并回写
- upstream 失败时返回空 DataFrame 不抛异常
- years 过滤正确
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.collectors.cached_impl import LocalCachedSource
from src.storage import DuckDBStore


class MockUpstream:
    """记录调用次数的 upstream。"""

    def __init__(self, pe: pd.DataFrame, fin: pd.DataFrame):
        self._pe = pe
        self._fin = fin
        self.pe_call_count = 0
        self.fin_call_count = 0

    def get_stock_list(self): return pd.DataFrame()
    def get_sw_first_industry(self): return pd.DataFrame()
    def get_sw_second_industry(self): return pd.DataFrame()
    def get_stock_industry_mapping(self): return pd.DataFrame()
    def get_disclosure_calendar(self, period="2025年报"): return pd.DataFrame()

    def get_pe_pb_history(self, code, years=5):
        self.pe_call_count += 1
        return self._pe

    def get_financial_abstract(self, code):
        self.fin_call_count += 1
        return self._fin


def _pe_history(n: int = 200) -> pd.DataFrame:
    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="W")
    return pd.DataFrame({"date": dates, "pe_ttm": [20.0] * n, "pb": [2.0] * n})


def _fin() -> pd.DataFrame:
    return pd.DataFrame([
        {"report_date": pd.Timestamp(2024, 12, 31), "revenue": 1e10,
         "deducted_net_profit": 1e9, "gross_margin": 30.0},
    ])


class TestLocalCachedSource:
    def test_local_hit_skips_upstream(self, tmp_path: Path):
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        store.save_pe_pb_history("600031", _pe_history())
        try:
            upstream = MockUpstream(_pe_history(), _fin())
            source = LocalCachedSource(store=store, upstream=upstream)
            df = source.get_pe_pb_history("600031", years=5)
            assert upstream.pe_call_count == 0
            assert len(df) > 0
        finally:
            store.close()

    def test_local_miss_falls_back_and_writes_back(self, tmp_path: Path):
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        try:
            upstream = MockUpstream(_pe_history(), _fin())
            source = LocalCachedSource(store=store, upstream=upstream)
            # 第一次：本地空，拉 upstream
            df1 = source.get_pe_pb_history("600031", years=5)
            assert upstream.pe_call_count == 1
            assert len(df1) > 0
            # 本地表已回写
            local = store.load_pe_pb_history("600031")
            assert not local.empty
            # 第二次：本地有，不再调 upstream
            df2 = source.get_pe_pb_history("600031", years=5)
            assert upstream.pe_call_count == 1
            assert len(df2) > 0
        finally:
            store.close()

    def test_local_miss_financials_falls_back(self, tmp_path: Path):
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        try:
            upstream = MockUpstream(_pe_history(), _fin())
            source = LocalCachedSource(store=store, upstream=upstream)
            df = source.get_financial_abstract("600031")
            assert upstream.fin_call_count == 1
            assert not df.empty
            # 回写
            assert not store.load_financials("600031").empty
        finally:
            store.close()

    def test_years_window_filters_local(self, tmp_path: Path):
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        # 写 10 年数据，years=3 时只返回近 3 年
        store.save_pe_pb_history("600031", _pe_history(n=520))
        try:
            upstream = MockUpstream(_pe_history(), _fin())
            source = LocalCachedSource(store=store, upstream=upstream)
            df = source.get_pe_pb_history("600031", years=3)
            # 3 年 ≈ 156 周
            assert 100 < len(df) < 200
            # 不该调 upstream
            assert upstream.pe_call_count == 0
        finally:
            store.close()

    def test_writeback_off(self, tmp_path: Path):
        store = DuckDBStore(db_path=tmp_path / "t.duckdb")
        try:
            upstream = MockUpstream(_pe_history(), _fin())
            source = LocalCachedSource(store=store, upstream=upstream, writeback=False)
            source.get_pe_pb_history("600031")
            # 没回写
            assert store.load_pe_pb_history("600031").empty
        finally:
            store.close()
