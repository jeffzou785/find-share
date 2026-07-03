"""A/H/港股代码映射导入脚本测试。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "import_global_stock_mappings",
        PROJECT_ROOT / "scripts" / "import_global_stock_mappings.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


import_global_stock_mappings = _load_script()


def test_write_template(tmp_path: Path):
    path = tmp_path / "global_stock_mappings.csv"
    assert import_global_stock_mappings.write_template(path) is True
    df = pd.read_csv(path)
    assert list(df.columns) == import_global_stock_mappings.TEMPLATE_COLUMNS
    assert import_global_stock_mappings.write_template(path) is False


def test_validate_mapping_rows_rejects_duplicate_normalized_hk_code():
    df = pd.DataFrame([
        {"hk_code": "2359.HK", "a_code": "603259"},
        {"hk_code": "02359", "a_code": "603259"},
    ])
    errors = import_global_stock_mappings.validate_mapping_rows(df)
    assert "duplicate_hk_code:02359" in errors


def test_validate_mapping_rows_accepts_valid_input():
    df = pd.DataFrame([
        {
            "hk_code": "2359.HK",
            "a_code": "603259",
            "name": "药明康德",
            "source": "manual",
            "hk_disclosure_source_gap": "true",
        }
    ])
    assert import_global_stock_mappings.validate_mapping_rows(df) == []
