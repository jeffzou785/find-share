"""P2-5 TushareSource stub 测试。"""
from __future__ import annotations

import pytest

from src.collectors import TushareSource


class TestTushareStub:
    def test_can_instantiate_without_token(self):
        """无 token 也允许实例化（用于类型检查、import）。"""
        source = TushareSource(token=None)
        assert source is not None
        assert source.token in (None, "")  # config.TUSHARE_TOKEN 默认空

    def test_methods_raise_not_implemented(self):
        """无 token 时调用方法抛 NotImplementedError，且提示激活路径。"""
        source = TushareSource(token=None)
        with pytest.raises(NotImplementedError, match="stub"):
            source.get_stock_list()
        with pytest.raises(NotImplementedError, match="stub"):
            source.get_pe_pb_history("600031", years=5)
        with pytest.raises(NotImplementedError, match="stub"):
            source.get_financial_abstract("600031")

    def test_implements_protocol_shape(self):
        """TushareSource 类应包含 DataSource Protocol 的所有方法名。"""
        from src.collectors.base import DataSource

        protocol_methods = {
            "get_stock_list", "get_sw_first_industry",
            "get_sw_second_industry", "get_stock_industry_mapping",
            "get_pe_pb_history", "get_financial_abstract",
            "get_disclosure_calendar",
        }
        for method in protocol_methods:
            assert hasattr(TushareSource, method), f"missing {method}"
        # 注：不强制 isinstance(TushareSource(), DataSource)，因为 runtime_checkable
        # Protocol 只校验方法存在性，但实例化不需要实际实现。
