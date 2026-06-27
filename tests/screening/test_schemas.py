"""metrics_json / config_json schema 测试（P0-3 / P0-4）。"""
from __future__ import annotations

import json

from src.screening.schemas import (
    ConfigSchema,
    MetricsSchema,
    RuntimeConfig,
    SourceStatus,
    Thresholds,
)


class TestMetricsSchema:
    def test_default_all_none(self):
        m = MetricsSchema()
        d = m.to_dict()
        assert d["valuation"]["pe_ttm"] is None
        assert d["growth"]["revenue_yoy"] is None
        assert d["quality"]["gross_margin"] is None
        assert d["overseas"]["overseas_ratio"] is None

    def test_json_serializable(self):
        m = MetricsSchema()
        parsed = json.loads(m.to_json())
        assert "valuation" in parsed
        assert "source_status" in parsed

    def test_partial_fill(self):
        m = MetricsSchema()
        m.valuation.pe_ttm = 25.3
        m.valuation.pe_pct_3y = 18.5
        m.overseas.overseas_ratio = 0.45
        m.overseas.parse_warning = "unit_ambiguous"
        d = m.to_dict()
        assert d["valuation"]["pe_ttm"] == 25.3
        assert d["overseas"]["parse_warning"] == "unit_ambiguous"
        # 未设置的字段仍是 None
        assert d["valuation"]["pb"] is None
        assert d["growth"]["revenue_yoy"] is None

    def test_source_status_defaults(self):
        s = SourceStatus()
        assert s.financials == "ok"
        assert s.consensus == "skipped"


class TestConfigSchema:
    def test_default_runtime(self):
        c = ConfigSchema(strategy="overseas", period="2025A")
        assert c.runtime.max_workers == 1
        assert c.runtime.retry_times == 2
        assert c.runtime.resume is False

    def test_json_roundtrip(self):
        c = ConfigSchema(strategy="consumer", period="2025A")
        c.thresholds = Thresholds(pe_ttm_max=25.0, deducted_yoy_min=0.3)
        c.runtime = RuntimeConfig(max_workers=2, retry_times=3)
        parsed = json.loads(c.to_json())
        assert parsed["strategy"] == "consumer"
        assert parsed["thresholds"]["pe_ttm_max"] == 25.0
        assert parsed["runtime"]["max_workers"] == 2

    def test_fingerprint_stable(self):
        """相同配置 → 相同 fingerprint。"""
        c1 = ConfigSchema(strategy="overseas", period="2025A")
        c1.thresholds = Thresholds(pe_ttm_max=25.0)
        c2 = ConfigSchema(strategy="overseas", period="2025A")
        c2.thresholds = Thresholds(pe_ttm_max=25.0)
        # runtime 不影响 fingerprint
        c2.runtime = RuntimeConfig(max_workers=8)
        assert c1.fingerprint() == c2.fingerprint()

    def test_fingerprint_differs_on_threshold(self):
        c1 = ConfigSchema(strategy="overseas", period="2025A")
        c1.thresholds = Thresholds(pe_ttm_max=25.0)
        c2 = ConfigSchema(strategy="overseas", period="2025A")
        c2.thresholds = Thresholds(pe_ttm_max=30.0)
        assert c1.fingerprint() != c2.fingerprint()

    def test_fingerprint_differs_on_strategy(self):
        c1 = ConfigSchema(strategy="consumer", period="2025A")
        c2 = ConfigSchema(strategy="overseas", period="2025A")
        assert c1.fingerprint() != c2.fingerprint()

    def test_score_weights_omitted_when_none(self):
        c = ConfigSchema()
        d = c.to_dict()
        assert "score_weights" not in d
