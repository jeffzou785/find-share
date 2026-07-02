"""P2-1 评分层测试。"""
from __future__ import annotations

import pytest

from src.screening.schemas import MetricsSchema
from src.screening.scoring import (
    DEFAULT_RISK_PENALTY_WEIGHT,
    DEFAULT_WEIGHTS_CONSUMER,
    DEFAULT_WEIGHTS_OVERSEAS,
    _avg,
    _clamp,
    _norm_high_better,
    _norm_low_better,
    compute_catalyst_score,
    compute_growth_score,
    compute_neglect_score,
    compute_quality_score,
    compute_risk_penalty,
    compute_score,
    compute_valuation_score,
    default_weights,
)


class TestNormHelpers:
    def test_clamp_basic(self):
        assert _clamp(0.5) == 0.5
        assert _clamp(-1.0) == 0.0
        assert _clamp(2.0) == 1.0

    def test_clamp_none(self):
        assert _clamp(None) is None

    def test_clamp_nan(self):
        assert _clamp(float("nan")) is None

    def test_norm_high_better(self):
        assert _norm_high_better(0.5, 0.0, 1.0) == 0.5
        assert _norm_high_better(1.5, 0.0, 1.0) == 1.5  # 不 clamp，由上层处理
        assert _norm_high_better(None, 0.0, 1.0) is None

    def test_norm_low_better(self):
        assert _norm_low_better(0.0, 0.0, 1.0) == 1.0
        assert _norm_low_better(1.0, 0.0, 1.0) == 0.0

    def test_avg_skips_none(self):
        assert _avg([0.5, None, 0.7]) == pytest.approx(0.6)
        assert _avg([None, None]) is None


class TestSubscores:
    def test_growth_score_consumer_basic(self):
        m = MetricsSchema()
        m.growth.deducted_profit_yoy_ttm = 1.0  # +100%
        m.growth.revenue_yoy = 0.5  # +50%
        s = compute_growth_score(m, "consumer")
        assert s is not None
        assert 0.5 < s <= 1.0

    def test_growth_score_overseas_includes_overseas_yoy(self):
        m = MetricsSchema()
        m.growth.deducted_profit_yoy_ttm = 0.5
        m.overseas.overseas_yoy = 1.0  # 海外同比 +100%
        s = compute_growth_score(m, "overseas")
        assert s is not None
        assert s > 0.5

    def test_growth_score_all_none(self):
        m = MetricsSchema()
        assert compute_growth_score(m, "consumer") is None

    def test_valuation_score_low_pe_high_score(self):
        m = MetricsSchema()
        m.valuation.pe_pct_5y = 10.0  # 分位 10%
        m.valuation.pe_ttm = 10.0
        s = compute_valuation_score(m)
        assert s is not None
        assert s > 0.7  # 低估应该高分

    def test_valuation_score_keeps_zero_percentile(self):
        m = MetricsSchema()
        m.valuation.pe_pct_5y = 0.0
        m.valuation.pe_pct_3y = 100.0
        m.valuation.pb_pct_5y = 0.0
        m.valuation.pb_pct_3y = 100.0
        s = compute_valuation_score(m)
        assert s == pytest.approx(1.0)

    def test_valuation_score_high_pe_low_score(self):
        m = MetricsSchema()
        m.valuation.pe_pct_5y = 90.0
        m.valuation.pe_ttm = 50.0
        s = compute_valuation_score(m)
        assert s is not None
        assert s < 0.3

    def test_quality_score_full(self):
        m = MetricsSchema()
        m.quality.gross_margin = 0.5
        m.quality.ocf_to_net_profit = 1.0
        m.quality.debt_ratio = 0.3
        s = compute_quality_score(m)
        assert s is not None
        assert s > 0.5

    def test_catalyst_score_with_consensus(self):
        m = MetricsSchema()
        m.catalyst.eps_y1_growth = 0.3
        m.catalyst.eps_y2_growth = 0.3
        m.catalyst.reports_count_90d = 5
        s = compute_catalyst_score(m)
        assert s is not None
        assert 0 < s <= 1.0

    def test_neglect_score_low_coverage_high_score(self):
        """研报少 + 新闻少 + 非 AI = 高分（被忽视）"""
        m = MetricsSchema()
        m.catalyst.reports_count_90d = 1
        m.catalyst.news_count_30d = 2
        m.catalyst.is_ai_related = False
        s = compute_neglect_score(m)
        assert s is not None
        assert s > 0.7

    def test_neglect_score_ai_related_zero_for_neglect(self):
        """AI 概念股的 neglect 子分 = 0"""
        m = MetricsSchema()
        m.catalyst.is_ai_related = True
        m.catalyst.reports_count_90d = 0
        m.catalyst.news_count_30d = 0
        s = compute_neglect_score(m)
        assert s is not None
        assert s < 0.7  # AI 拉低

    def test_risk_penalty_parse_warning(self):
        m = MetricsSchema()
        m.overseas.parse_warning = "unit_ambiguous"
        assert compute_risk_penalty(m) == 0.3

    def test_risk_penalty_accumulates_parse_warning_quality_risks(self):
        m = MetricsSchema()
        m.overseas.parse_warning = "unit_ambiguous"
        m.quality.debt_ratio = 0.8
        m.quality.ocf_to_net_profit = 0.1
        assert compute_risk_penalty(m) == pytest.approx(0.7)

    def test_risk_penalty_high_debt_low_ocf(self):
        m = MetricsSchema()
        m.quality.debt_ratio = 0.8
        m.quality.ocf_to_net_profit = 0.1
        assert compute_risk_penalty(m) == 0.4

    def test_risk_penalty_clean_zero(self):
        m = MetricsSchema()
        m.quality.debt_ratio = 0.3
        m.quality.ocf_to_net_profit = 1.0
        assert compute_risk_penalty(m) == 0.0


class TestComputeScore:
    def test_consumer_default_weights(self):
        assert default_weights("consumer") == DEFAULT_WEIGHTS_CONSUMER
        assert default_weights("consumer")["neglect"] == 0.0

    def test_overseas_default_weights(self):
        assert default_weights("overseas") == DEFAULT_WEIGHTS_OVERSEAS
        assert default_weights("overseas")["neglect"] == 0.2

    def test_overseas_full_metrics_produces_score(self):
        m = MetricsSchema()
        m.valuation.pe_ttm = 15.0
        m.valuation.pe_pct_5y = 20.0
        m.valuation.pb_pct_5y = 15.0
        m.growth.deducted_profit_yoy_ttm = 0.5
        m.growth.revenue_yoy = 0.3
        m.overseas.overseas_yoy = 0.6
        m.overseas.overseas_ratio = 0.5
        m.quality.gross_margin = 0.4
        m.quality.ocf_to_net_profit = 0.9
        m.quality.debt_ratio = 0.4
        m.catalyst.reports_count_90d = 2
        m.catalyst.news_count_30d = 5
        m.catalyst.is_ai_related = False
        s = compute_score(m, "overseas")
        assert s.final_score is not None
        assert 0 < s.final_score <= 1.0
        assert s.growth_score is not None
        assert s.valuation_score is not None
        assert s.neglect_score is not None
        assert s.coverage_ratio == pytest.approx(1.0)
        assert s.weights_used == DEFAULT_WEIGHTS_OVERSEAS

    def test_consumer_full_metrics_produces_score(self):
        m = MetricsSchema()
        m.valuation.pe_ttm = 12.0
        m.valuation.pe_pct_5y = 15.0
        m.growth.deducted_profit_yoy_ttm = 0.6
        m.growth.revenue_yoy = 0.3
        m.quality.gross_margin = 0.5
        m.catalyst.reports_count_90d = 8
        s = compute_score(m, "consumer")
        assert s.final_score is not None
        # neglect 权重为 0，consumer 评分应不依赖 neglect
        assert s.coverage_ratio == pytest.approx(1.0)
        assert s.weights_used == DEFAULT_WEIGHTS_CONSUMER

    def test_empty_metrics_returns_none_final(self):
        m = MetricsSchema()
        s = compute_score(m, "consumer")
        # 所有子分 None → final_score None
        assert s.final_score is None
        assert s.coverage_ratio == pytest.approx(0.0)

    def test_missing_subscore_uses_neutral_fill_and_reports_coverage(self):
        m = MetricsSchema()
        m.growth.deducted_profit_yoy_ttm = 2.0
        custom = {"growth": 0.5, "valuation": 0.5, "quality": 0.0,
                  "catalyst": 0.0, "neglect": 0.0}
        s = compute_score(m, "consumer", weights=custom)
        assert s.growth_score == pytest.approx(1.0)
        assert s.valuation_score is None
        assert s.coverage_ratio == pytest.approx(0.5)
        assert s.final_score == pytest.approx(0.75)

    def test_custom_weights_override(self):
        m = MetricsSchema()
        m.growth.deducted_profit_yoy_ttm = 0.5
        custom = {"growth": 1.0, "valuation": 0.0, "quality": 0.0,
                  "catalyst": 0.0, "neglect": 0.0}
        s = compute_score(m, "consumer", weights=custom)
        assert s.final_score is not None
        assert s.weights_used == custom

    def test_risk_penalty_reduces_final_score(self):
        m1 = MetricsSchema()
        m1.valuation.pe_ttm = 15.0
        m1.valuation.pe_pct_5y = 20.0
        m1.growth.deducted_profit_yoy_ttm = 0.5
        s1 = compute_score(m1, "consumer")

        m2 = MetricsSchema()
        m2.valuation.pe_ttm = 15.0
        m2.valuation.pe_pct_5y = 20.0
        m2.growth.deducted_profit_yoy_ttm = 0.5
        m2.overseas.parse_warning = "unit_ambiguous"  # 触发 0.3 风险扣分
        s2 = compute_score(m2, "consumer")

        assert s1.final_score > s2.final_score
        # 扣分幅度 = 0.3 * DEFAULT_RISK_PENALTY_WEIGHT
        diff = s1.final_score - s2.final_score
        assert diff == pytest.approx(0.3 * DEFAULT_RISK_PENALTY_WEIGHT, abs=0.01)

    def test_score_clamped_to_0_1(self):
        m = MetricsSchema()
        m.growth.deducted_profit_yoy_ttm = 5.0  # 远超归一区间
        m.growth.revenue_yoy = 5.0
        s = compute_score(m, "consumer")
        assert s.final_score is not None
        assert s.final_score <= 1.0
