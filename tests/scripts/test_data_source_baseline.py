"""P1-5a 数据源 baseline 工具单元测试。

只测纯函数（不触网）：
- _diff_pct：相对差异计算
- _sample_codes：抽样逻辑
- write_summary：Markdown 输出格式
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import importlib.util
import pandas as pd

_spec = importlib.util.spec_from_file_location(
    "data_source_baseline",
    PROJECT_ROOT / "scripts" / "data_source_baseline.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_diff_pct = _mod._diff_pct
write_summary = _mod.write_summary
DIFF_THRESHOLD_PCT = _mod.DIFF_THRESHOLD_PCT


class TestDiffPct:
    def test_both_none(self):
        assert _diff_pct(None, None) is None

    def test_one_none(self):
        assert _diff_pct(1.0, None) is None
        assert _diff_pct(None, 1.0) is None

    def test_both_zero(self):
        assert _diff_pct(0.0, 0.0) == 0.0

    def test_equal(self):
        assert _diff_pct(100.0, 100.0) == 0.0

    def test_diff(self):
        # 100 vs 110：相对差 10/110 ≈ 0.0909
        assert _diff_pct(100.0, 110.0) == 10 / 110

    def test_negative_values(self):
        # -100 vs -110：相对差 10/110
        assert _diff_pct(-100.0, -110.0) == 10 / 110

    def test_one_negative(self):
        # 100 vs -100：相对差 200/100 = 2.0
        assert _diff_pct(100.0, -100.0) == 2.0


class TestWriteSummary:
    def test_no_data(self, tmp_path: Path):
        df = pd.DataFrame(columns=["code", "field", "akshare_value", "sina_value", "diff_pct"])
        out_dir = tmp_path / "baseline_test"
        write_summary(df, out_dir, ["600519"])
        summary = (out_dir / "summary.md").read_text(encoding="utf-8")
        assert "数据源 baseline 对照" in summary

    def test_renders_diff_distribution(self, tmp_path: Path):
        df = pd.DataFrame([
            {"code": "600519", "field": "revenue",
             "akshare_value": 100.0, "sina_value": 105.0, "diff_pct": 5/105},
            {"code": "000858", "field": "revenue",
             "akshare_value": 100.0, "sina_value": 200.0, "diff_pct": 0.5},
            {"code": "600519", "field": "net_profit",
             "akshare_value": 10.0, "sina_value": 10.0, "diff_pct": 0.0},
        ])
        out_dir = tmp_path / "baseline_test"
        write_summary(df, out_dir, ["600519", "000858"])
        summary = (out_dir / "summary.md").read_text(encoding="utf-8")
        # 差异分布表存在
        assert "差异分布" in summary
        assert "revenue" in summary
        # 不可替换清单：revenue 有 1 只（000858 diff=50%）
        assert "不可替换" in summary
        assert "000858" in summary

    def test_missing_values_section(self, tmp_path: Path):
        df = pd.DataFrame([
            {"code": "600519", "field": "revenue",
             "akshare_value": None, "sina_value": 100.0, "diff_pct": None},
        ])
        out_dir = tmp_path / "baseline_test"
        write_summary(df, out_dir, ["600519"])
        summary = (out_dir / "summary.md").read_text(encoding="utf-8")
        assert "单源缺失" in summary
