"""港股代码格式归一测试。"""
from __future__ import annotations

import pandas as pd
import pytest

from src.collectors.global_stock_mapping import (
    build_hk_mapping_frame,
    build_hk_mapping,
    hk_eastmoney_secucode,
    hk_eastmoney_secid,
    hk_yahoo_symbol,
    normalize_a_code,
    normalize_hk_code,
)


def test_normalize_hk_code_to_five_digits():
    assert normalize_hk_code("700") == "00700"
    assert normalize_hk_code("0700.HK") == "00700"
    assert normalize_hk_code("HK:9988") == "09988"


def test_hk_vendor_symbols():
    assert hk_yahoo_symbol("00700") == "0700.HK"
    assert hk_yahoo_symbol("09988") == "9988.HK"
    assert hk_eastmoney_secucode("700") == "00700.HK"
    assert hk_eastmoney_secid("700") == "116.00700"


def test_build_hk_mapping_marks_disclosure_gap():
    mapping = build_hk_mapping(a_code="688235", hk_code="02359", name="药明康德")
    assert mapping.a_code == "688235"
    assert mapping.hk_code == "02359"
    assert mapping.yahoo_symbol == "2359.HK"
    assert mapping.eastmoney_secucode == "02359.HK"
    assert mapping.eastmoney_secid == "116.02359"
    assert mapping.hk_disclosure_source_gap is True


def test_normalize_a_code():
    assert normalize_a_code("1") == "000001"
    assert normalize_a_code("SH600519") == "600519"
    assert normalize_a_code(None) is None


def test_invalid_hk_code_raises():
    with pytest.raises(ValueError):
        normalize_hk_code("abcdef")


def test_build_hk_mapping_frame_normalizes_vendor_symbols_and_bool_text():
    df = build_hk_mapping_frame(pd.DataFrame([
        {
            "a_code": "SH603259",
            "hk_code": "2359.HK",
            "name": "药明康德",
            "source": "manual",
            "hk_disclosure_source_gap": "false",
        }
    ]))
    row = df.iloc[0]
    assert row["a_code"] == "603259"
    assert row["hk_code"] == "02359"
    assert row["yahoo_symbol"] == "2359.HK"
    assert row["eastmoney_secid"] == "116.02359"
    assert bool(row["hk_disclosure_source_gap"]) is False


def test_build_hk_mapping_frame_defaults_blank_optional_fields():
    df = build_hk_mapping_frame(pd.DataFrame([
        {"a_code": "", "hk_code": "700", "name": "", "source": ""}
    ]))
    row = df.iloc[0]
    assert row["a_code"] is None
    assert row["name"] is None
    assert row["source"] == "manual"
