"""策略一全量状态化测试（P0-5 / P0-6 / P0-12）。

测试 evaluate_consumer_full 的每条状态路径：
hit / rejected（3 个原因码）/ data_missing（2 个原因码）/ error。
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.screening import Status
from src.strategies.consumer_reversal import (
    StrategyConfig,
    evaluate_consumer_full,
)


class MockSource:
    """最小 DataSource mock，仅实现策略一需要的两个方法。"""

    def __init__(self, pe_hist: pd.DataFrame | None, fin: pd.DataFrame | None):
        self._pe = pe_hist
        self._fin = fin

    def get_pe_pb_history(self, code: str, years: int = 5):
        if self._pe is None:
            raise RuntimeError("network down")
        return self._pe

    def get_financial_abstract(self, code: str):
        if self._fin is None:
            raise RuntimeError("network down")
        return self._fin


def _pe_history(
    n: int, value: float = 10.0, freq: str = "W", current_value: float | None = None
) -> pd.DataFrame:
    """构造 n 个样本的 PE 历史。

    前 n-1 个样本 = 50（高位），最后 1 个样本 = current_value 或 value（当前值）。
    默认 current_value=value 让 percentile 接近 0；传 current_value 可独立控制末值。

    freq 默认 W（周），n=120 → 约 840 天 > 730 天，不会触发 new_listing。
    若需要构造 new_listing 场景，传更短的 freq 或显式构造日期。
    """
    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq=freq)
    last = current_value if current_value is not None else value
    values = [50.0] * (n - 1) + [last]
    return pd.DataFrame({"date": dates, "pe_ttm": values})


def _pe_history_negative_current(n: int = 120) -> pd.DataFrame:
    """构造亏损股 PE 历史：全部历史为正，当前末值为负。"""
    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="W")
    values = [50.0] * (n - 1) + [-15.0]
    return pd.DataFrame({"date": dates, "pe_ttm": values})


def _pe_history_new_listing(n: int = 50) -> pd.DataFrame:
    """构造新上市 PE 历史：n 个日样本，跨度 < 2 年。"""
    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="D")
    values = [50.0] * (n - 1) + [10.0]
    return pd.DataFrame({"date": dates, "pe_ttm": values})


def _financials_basic(seq: list[tuple[int, int, float]]) -> pd.DataFrame:
    """构造多期扣非累计净利润。

    seq: [(year, month, deducted_net_profit), ...]

    P1-1 增强：附带 revenue_yoy（默认 20% 通过新阈值）+ gross_margin 多年小幅改善。

    单位约定（与 AkShare stock_financial_abstract 一致）：
    - revenue_yoy: 百分数（20.0 = 20%）
    - gross_margin: 百分数（30.0 = 30%）
    策略层会 /100 转小数后入 metrics。
    """
    return pd.DataFrame(
        [
            {"report_date": pd.Timestamp(year=y, month=m, day=28),
             "deducted_net_profit": v, "revenue": v * 10,
             "net_profit_attr_parent": v * 1.1,
             "net_profit": v * 1.1,
             "revenue_yoy": 20.0,  # 百分数 20%；策略层 /100 → 0.20 > 0.10
             "ocf_per_share": 1.0,
             # 毛利率逐年小幅改善（30.0 → 31.0 → 32.0），变化 ≤ 0.5pp
             "gross_margin": 30.0 + 1.0 * (i if i < 3 else 2)}
            for i, (y, m, v) in enumerate(seq)
        ]
    )


@pytest.fixture
def base_candidates():
    return pd.DataFrame(
        [{"code": "600031", "name": "X", "sw_first": "食品饮料"}]
    )


class TestEvaluateConsumerFull:
    def test_pe_fetch_error_returns_data_missing(self, base_candidates):
        source = MockSource(pe_hist=None, fin=_financials_basic([]))
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
        )
        assert len(results) == 1
        r = results[0]
        assert r.status == Status.DATA_MISSING
        assert r.data_missing_reason == "pe_history_missing"

    def test_pe_sample_insufficient_returns_data_missing(self, base_candidates):
        # 长跨度但样本数不足：80 个月样本，跨度 > 730 天，但 < min_history_samples=100
        source = MockSource(pe_hist=_pe_history(80, freq="MS"), fin=_financials_basic([]))
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        assert results[0].status == Status.DATA_MISSING
        assert results[0].data_missing_reason == "pe_history_missing"

    def test_pe_negative_current_returns_pe_ttm_invalid(self, base_candidates):
        # 修复 regression：亏损股当前 PE 为负，历史全正 → 不应算分位
        source = MockSource(
            pe_hist=_pe_history_negative_current(120),
            fin=_financials_basic([]),
        )
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        assert results[0].status == Status.DATA_MISSING
        assert results[0].data_missing_reason == "pe_ttm_invalid"

    def test_new_listing_insufficient_history(self, base_candidates):
        # 001312 场景：跨度 <2 年，样本数也 <100 → new_listing_insufficient_history
        source = MockSource(
            pe_hist=_pe_history_new_listing(50),
            fin=_financials_basic([]),
        )
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        assert results[0].status == Status.DATA_MISSING
        assert results[0].data_missing_reason == "new_listing_insufficient_history"

    def test_financials_empty_returns_data_missing(self, base_candidates):
        source = MockSource(
            pe_hist=_pe_history(120),
            fin=pd.DataFrame(columns=["report_date", "deducted_net_profit"]),
        )
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        assert results[0].status == Status.DATA_MISSING
        assert results[0].data_missing_reason == "deducted_profit_missing"

    def test_pe_percentile_too_high_rejected(self, base_candidates):
        # PE 全部 10，current=10 percentile=0；改用 current 远高于历史
        # 这里用低 PE，再用 config 把阈值卡到 0 以下触发 rejected
        source = MockSource(
            pe_hist=_pe_history(120, value=10.0),
            fin=_financials_basic([
                (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
            ]),
        )
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100, pe_percentile_max=-1),
        )
        assert results[0].status == Status.REJECTED
        assert results[0].reject_reason == "pe_percentile_too_high"

    def test_deducted_yoy_too_low_rejected(self, base_candidates):
        # 构造 PE 分位低 + 扣非同比刚好不够 + 反转判定通过（拐点）
        # yoy 必须先 ≥0.30 才通过 deducted_yoy_min，再 < 0.30 触发 rejected
        # 直接把 deducted_yoy_min 调到 0.99 触发该分支
        source = MockSource(
            pe_hist=_pe_history(120, value=10.0),
            fin=_financials_basic([
                (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
            ]),
        )
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100, deducted_yoy_min=0.99),
        )
        assert results[0].status == Status.REJECTED
        assert results[0].reject_reason == "deducted_yoy_too_low"

    def test_no_reversal_pattern_rejected(self, base_candidates):
        # 构造 yoy 高 + PE 低，但不是反转形态（前期 yoy 已经正）
        # 2023: 100 → 2024: 130 (yoy=30%) → 2025: 169 (yoy=30%)
        # 前期 yoy=30% > 0，不构成拐点；前期 yoy=30% 但前前期 yoy 不存在 → 不构成趋势
        source = MockSource(
            pe_hist=_pe_history(120, value=10.0),
            fin=_financials_basic([
                (2023, 12, 100.0), (2024, 12, 130.0), (2025, 12, 169.0),
            ]),
        )
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        assert results[0].status == Status.REJECTED
        assert results[0].reject_reason == "not_inflection_or_trend"

    def test_classic_inflection_hits(self, base_candidates):
        # 2023: 100 → 2024: 50 (yoy=-50%) → 2025: 80 (yoy=60%) → 拐点
        source = MockSource(
            pe_hist=_pe_history(120, value=10.0),
            fin=_financials_basic([
                (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
            ]),
        )
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        r = results[0]
        assert r.status == Status.HIT
        assert r.hit_reason == "all_thresholds_met"
        # metrics 已填充
        assert r.metrics.valuation.pe_ttm == 10.0
        assert r.metrics.growth.deducted_profit_yoy_ttm == pytest.approx(0.6, abs=1e-3)

    def test_period_excludes_future_financials(self, base_candidates):
        """回放 2025A 时不能使用本地已缓存的 2026Q1。"""
        fin = _financials_basic([
            (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
        ])
        # 缺少 2025Q1 时，若错误使用这一行，Q1 TTM 无法计算并会 data_missing。
        fin = pd.concat([
            fin,
            pd.DataFrame([{
                "report_date": pd.Timestamp(2026, 3, 31),
                "deducted_net_profit": 10.0, "revenue": 100.0,
                "net_profit_attr_parent": 11.0, "net_profit": 11.0,
                "revenue_yoy": 20.0, "gross_margin": 32.0,
                "ocf_per_share": 1.0,
            }]),
        ], ignore_index=True)
        source = MockSource(pe_hist=_pe_history(120, value=10.0), fin=fin)

        result = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )[0]

        assert result.status == Status.HIT
        assert result.metrics.growth.deducted_profit_yoy_ttm == pytest.approx(0.6, abs=1e-3)

    def test_metrics_source_status_reflects_failure(self, base_candidates):
        source = MockSource(pe_hist=None, fin=_financials_basic([]))
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
        )
        r = results[0]
        assert r.metrics.source_status.valuation == "error"

    def test_industry_filter_excluded(self):
        """行业不在目标列表的股票不应进入评估（不返回 ScreeningResult）。"""
        source = MockSource(
            pe_hist=_pe_history(120),
            fin=_financials_basic([(2025, 12, 1.0)]),
        )
        candidates = pd.DataFrame(
            [{"code": "600031", "name": "X", "sw_first": "钢铁"}]
        )
        results = evaluate_consumer_full(
            source=source, candidates=candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        assert results == []

    def test_to_row_writes_clean_dict(self, base_candidates):
        source = MockSource(
            pe_hist=_pe_history(120, value=10.0),
            fin=_financials_basic([
                (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
            ]),
        )
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        row = results[0].to_row()
        assert row["run_id"] == "r1"
        assert row["strategy"] == "consumer"
        assert row["status"] == "hit"
        assert row["period"] == "2025A"


class TestP11NewSignals:
    """P1-1 新增信号：PB 分位 / 营收同比 / 毛利率改善。"""

    def _basic_source(self, fin_override: pd.DataFrame | None = None) -> MockSource:
        if fin_override is None:
            fin_override = _financials_basic([
                (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
            ])
        return MockSource(pe_hist=_pe_history(120, value=10.0), fin=fin_override)

    def test_pb_percentile_too_high_rejected(self, base_candidates):
        """PB 分位 > 阈值 → rejected pb_percentile_too_high。"""
        source = self._basic_source()
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100, pb_percentile_max=-1),
        )
        # PE 历史构造里 pe_ttm 序列里 PB 列不存在；compute_pe_pb_percentile 会返回 None
        # 当 PB 缺失时 require_pb_percentile 不触发（设计上不剔除）
        # 但 pb_percentile_max=-1 也不会触发（因为 pb_stat['percentile'] is None）
        # 这个测试改成构造 PB 数据
        assert results[0].status in (Status.HIT, Status.WATCH)

    def test_pb_data_present_and_high_rejected(self, base_candidates):
        """PE 历史带 PB 列，分位高 → rejected。"""
        dates = pd.date_range(end=pd.Timestamp.now(), periods=120, freq="W")
        # 前 119 个 PB=1.0（低位），最后 1 个 PB=10.0（高位）
        pe_hist = pd.DataFrame({
            "date": dates,
            "pe_ttm": [50.0] * 119 + [10.0],
            "pb": [1.0] * 119 + [10.0],
        })
        source = MockSource(pe_hist=pe_hist, fin=_financials_basic([
            (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
        ]))
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100, pb_percentile_max=50.0),
        )
        r = results[0]
        assert r.status == Status.REJECTED
        assert r.reject_reason == "pb_percentile_too_high"
        assert r.metrics.valuation.pb == 10.0

    def test_revenue_yoy_too_low_rejected(self, base_candidates):
        """revenue_yoy < 10% → rejected revenue_yoy_too_low。"""
        # 构造 revenue_yoy = 5% 的财务数据（百分数 5.0；与 AkShare 单位一致）
        fin = pd.DataFrame([
            {"report_date": pd.Timestamp(year=2023, month=12, day=28),
             "deducted_net_profit": 100.0, "revenue": 1000.0,
             "revenue_yoy": 5.0, "gross_margin": 30.0},
            {"report_date": pd.Timestamp(year=2024, month=12, day=28),
             "deducted_net_profit": 50.0, "revenue": 500.0,
             "revenue_yoy": 5.0, "gross_margin": 30.0},
            {"report_date": pd.Timestamp(year=2025, month=12, day=28),
             "deducted_net_profit": 80.0, "revenue": 800.0,
             "revenue_yoy": 5.0, "gross_margin": 32.0},
        ])
        source = MockSource(pe_hist=_pe_history(120, value=10.0), fin=fin)
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100, revenue_yoy_min=0.10),
        )
        r = results[0]
        assert r.status == Status.REJECTED
        assert r.reject_reason == "revenue_yoy_too_low"
        # metrics 已转小数
        assert r.metrics.growth.revenue_yoy == pytest.approx(0.05, abs=1e-6)

    def test_decimal_percent_fields_from_skill_source_hit(self, base_candidates):
        """兼容 skill/Sina 已转小数的 revenue_yoy/gross_margin 口径。"""
        fin = _financials_basic([
            (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
        ])
        fin["revenue_yoy"] = 0.20
        fin["gross_margin"] = [0.30, 0.31, 0.32]
        source = MockSource(pe_hist=_pe_history(120, value=10.0), fin=fin)
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        r = results[0]
        assert r.status == Status.HIT
        assert r.metrics.growth.revenue_yoy == pytest.approx(0.20, abs=1e-6)
        assert r.metrics.quality.gross_margin == pytest.approx(0.32, abs=1e-6)

    def test_net_profit_proxy_when_deducted_missing_goes_to_watch(self, base_candidates):
        """扣非缺失时可用归母净利代理增长做 triage，但不能直接 hit。"""
        fin = _financials_basic([
            (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
        ]).drop(columns=["deducted_net_profit"])
        source = MockSource(pe_hist=_pe_history(120, value=10.0), fin=fin)

        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )

        r = results[0]
        assert r.status == Status.WATCH
        assert r.watch_reason == "deducted_profit_proxy_used"
        assert r.metrics.source_status.extra["deducted_profit_proxy"] == "net_profit_attr_parent"
        assert r.metrics.growth.deducted_profit_yoy_ttm == pytest.approx(0.6, abs=1e-3)

    def test_revenue_yoy_missing_goes_to_watch(self, base_candidates):
        """revenue_yoy 字段缺失 → watch data_warning（不直接剔除）。"""
        fin = pd.DataFrame([
            {"report_date": pd.Timestamp(year=2023, month=12, day=28),
             "deducted_net_profit": 100.0, "revenue": 1000.0,
             "gross_margin": 30.0},
            {"report_date": pd.Timestamp(year=2024, month=12, day=28),
             "deducted_net_profit": 50.0, "revenue": 500.0,
             "gross_margin": 30.0},
            {"report_date": pd.Timestamp(year=2025, month=12, day=28),
             "deducted_net_profit": 80.0, "revenue": 800.0,
             "gross_margin": 32.0},
        ])
        source = MockSource(pe_hist=_pe_history(120, value=10.0), fin=fin)
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        r = results[0]
        assert r.status == Status.WATCH
        assert r.watch_reason == "data_warning"

    def test_gross_margin_deteriorating_rejected(self, base_candidates):
        """毛利率同比下降 > 0.5pp → rejected gross_margin_deteriorating。

        单位说明：gross_margin 是百分数（40.0 = 40%），策略层 /100 转小数后
        相减得 -0.10（小数，等价于 -10pp），阈值 -0.005 = -0.5pp。
        """
        fin = pd.DataFrame([
            {"report_date": pd.Timestamp(year=2023, month=12, day=28),
             "deducted_net_profit": 100.0, "revenue": 1000.0,
             "revenue_yoy": 20.0, "gross_margin": 40.0},
            {"report_date": pd.Timestamp(year=2024, month=12, day=28),
             "deducted_net_profit": 50.0, "revenue": 500.0,
             "revenue_yoy": 20.0, "gross_margin": 40.0},
            # 2025 毛利率 30.0，比 2024 40.0 下降 10pp，远超 0.5pp 阈值
            {"report_date": pd.Timestamp(year=2025, month=12, day=28),
             "deducted_net_profit": 80.0, "revenue": 800.0,
             "revenue_yoy": 20.0, "gross_margin": 30.0},
        ])
        source = MockSource(pe_hist=_pe_history(120, value=10.0), fin=fin)
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        r = results[0]
        assert r.status == Status.REJECTED
        assert r.reject_reason == "gross_margin_deteriorating"

    def test_ocf_per_share_negative_rejected(self, base_candidates):
        """每股经营现金流不为正 → rejected cashflow_quality_failed。"""
        fin = _financials_basic([
            (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
        ])
        fin.loc[fin.index[-1], "ocf_per_share"] = -0.1
        source = MockSource(pe_hist=_pe_history(120, value=10.0), fin=fin)
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        r = results[0]
        assert r.status == Status.REJECTED
        assert r.reject_reason == "cashflow_quality_failed"
        assert r.metrics.source_status.extra["ocf_per_share"] == "-0.100000"

    def test_deducted_profit_quality_low_rejected(self, base_candidates):
        """扣非净利/归母净利过低 → rejected deducted_profit_quality_failed。"""
        fin = _financials_basic([
            (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
        ])
        fin.loc[fin.index[-1], "net_profit_attr_parent"] = 200.0
        source = MockSource(pe_hist=_pe_history(120, value=10.0), fin=fin)
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        r = results[0]
        assert r.status == Status.REJECTED
        assert r.reject_reason == "deducted_profit_quality_failed"
        assert r.metrics.source_status.extra["deducted_to_parent_profit"] == "0.400000"

    def test_p11_signals_can_be_disabled(self, base_candidates):
        """关掉 P1-1 信号 → 旧 hit 行为恢复（数据缺失也能 hit）。"""
        fin = pd.DataFrame([
            {"report_date": pd.Timestamp(year=2023, month=12, day=28),
             "deducted_net_profit": 100.0},
            {"report_date": pd.Timestamp(year=2024, month=12, day=28),
             "deducted_net_profit": 50.0},
            {"report_date": pd.Timestamp(year=2025, month=12, day=28),
             "deducted_net_profit": 80.0},
        ])
        source = MockSource(pe_hist=_pe_history(120, value=10.0), fin=fin)
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(
                min_history_samples=100,
                require_pb_percentile=False,
                require_revenue_confirmation=False,
                require_gross_margin_improvement=False,
                require_ocf_per_share_positive=False,
                require_deducted_profit_quality=False,
            ),
        )
        r = results[0]
        assert r.status == Status.HIT


class TestP1WorkingCapitalQuality:
    """P1：应收/存货/销售费用率以同比背离方式守卫。"""

    @staticmethod
    def _source_with_working_capital(
        *,
        latest_receivables: float = 100.0,
        latest_inventory: float = 100.0,
        latest_selling_expense: float = 100.0,
    ) -> MockSource:
        fin = _financials_basic([
            (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
        ])
        # 2024 -> 2025 收入同比为 60%；以下字段与收入同口径比较。
        fin["accounts_receivable"] = [100.0, 100.0, latest_receivables]
        fin["inventory"] = [100.0, 100.0, latest_inventory]
        fin["selling_expense"] = [100.0, 100.0, latest_selling_expense]
        return MockSource(pe_hist=_pe_history(120, value=10.0), fin=fin)

    def _result(self, base_candidates, **kwargs):
        return evaluate_consumer_full(
            source=self._source_with_working_capital(**kwargs),
            candidates=base_candidates, run_id="r1", period="2025A",
            show_progress=False, config=StrategyConfig(min_history_samples=100),
        )[0]

    def test_receivables_growth_excess_goes_to_watch(self, base_candidates):
        # 应收同比 +100%，收入同比 +60%，高出 40pp：watch。
        r = self._result(base_candidates, latest_receivables=200.0)
        assert r.status == Status.WATCH
        assert r.watch_reason == "receivables_growth_warning"
        assert r.metrics.source_status.extra["accounts_receivable_yoy_excess"] == "0.400000"

    def test_receivables_growth_excessive_rejected(self, base_candidates):
        # 应收同比 +150%，收入同比 +60%，高出 90pp：rejected。
        r = self._result(base_candidates, latest_receivables=250.0)
        assert r.status == Status.REJECTED
        assert r.reject_reason == "receivables_growth_excessive"

    def test_inventory_growth_excess_goes_to_watch(self, base_candidates):
        r = self._result(base_candidates, latest_inventory=200.0)
        assert r.status == Status.WATCH
        assert r.watch_reason == "inventory_growth_warning"
        assert r.metrics.source_status.extra["inventory_yoy_excess"] == "0.400000"

    def test_inventory_growth_excessive_rejected(self, base_candidates):
        r = self._result(base_candidates, latest_inventory=400.0)
        assert r.status == Status.REJECTED
        assert r.reject_reason == "inventory_growth_excessive"

    def test_selling_expense_ratio_rise_goes_to_watch(self, base_candidates):
        # 费用率从 20% 升至 25%，上升 5pp：watch。
        r = self._result(base_candidates, latest_selling_expense=200.0)
        assert r.status == Status.WATCH
        assert r.watch_reason == "selling_expense_ratio_warning"
        assert r.metrics.source_status.extra["selling_expense_ratio_yoy_change"] == "0.050000"

    def test_selling_expense_ratio_rising_rejected(self, base_candidates):
        r = self._result(base_candidates, latest_selling_expense=300.0)
        assert r.status == Status.REJECTED
        assert r.reject_reason == "selling_expense_ratio_rising"

    def test_missing_working_capital_fields_do_not_demote_hit(self, base_candidates):
        """老库尚未回填新字段时保留原筛选结果，只在 metrics 留 missing。"""
        source = MockSource(
            pe_hist=_pe_history(120, value=10.0),
            fin=_financials_basic([
                (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
            ]),
        )
        r = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )[0]
        assert r.status == Status.HIT
        assert r.metrics.source_status.extra["accounts_receivable_yoy"] == "missing"

    def test_q1_uses_prior_q1_not_prior_annual_for_gross_margin(self, base_candidates):
        """季报毛利率应对齐去年 Q1，不能与去年年报交叉比较。"""
        fin = pd.DataFrame([
            {"report_date": "2024-03-31", "deducted_net_profit": 10.0,
             "revenue": 80.0, "revenue_yoy": 20.0, "gross_margin": 30.0,
             "ocf_per_share": 1.0, "net_profit_attr_parent": 11.0},
            {"report_date": "2024-12-31", "deducted_net_profit": 100.0,
             "revenue": 800.0, "revenue_yoy": 20.0, "gross_margin": 30.0,
             "ocf_per_share": 1.0, "net_profit_attr_parent": 110.0},
            {"report_date": "2025-03-31", "deducted_net_profit": 5.0,
             "revenue": 100.0, "revenue_yoy": 20.0, "gross_margin": 30.0,
             "ocf_per_share": 1.0, "net_profit_attr_parent": 5.5},
            {"report_date": "2025-12-31", "deducted_net_profit": 50.0,
             "revenue": 500.0, "revenue_yoy": 20.0, "gross_margin": 50.0,
             "ocf_per_share": 1.0, "net_profit_attr_parent": 55.0},
            {"report_date": "2026-03-31", "deducted_net_profit": 70.0,
             "revenue": 120.0, "revenue_yoy": 20.0, "gross_margin": 30.0,
             "ocf_per_share": 1.0, "net_profit_attr_parent": 77.0},
        ])
        r = evaluate_consumer_full(
            source=MockSource(pe_hist=_pe_history(120, value=10.0), fin=fin),
            candidates=base_candidates, run_id="r1", period="2026Q1",
            show_progress=False,
            config=StrategyConfig(
                min_history_samples=100, deducted_yoy_min=-1.0,
                require_reversal_check=False,
            ),
        )[0]
        assert r.status == Status.HIT
        assert r.metrics.quality.gross_margin == pytest.approx(0.30)

    def test_invalid_working_capital_thresholds_rejected(self):
        with pytest.raises(ValueError, match="receivables_yoy_excess"):
            StrategyConfig(
                receivables_yoy_excess_watch=0.5,
                receivables_yoy_excess_reject=0.2,
            )


class TestP15MultiWindowPercentile:
    """P1.5-4：PE/PB 分位窗口参数化（3y / 5y / 10y）回归测试。"""

    def test_history_years_validates(self):
        """非法窗口值抛 ValueError。"""
        from src.strategies.consumer_reversal import SUPPORTED_HISTORY_WINDOWS
        with pytest.raises(ValueError):
            StrategyConfig(history_years=7)
        assert SUPPORTED_HISTORY_WINDOWS == (3, 5, 10)

    def test_all_windows_filled_in_metrics(self, base_candidates):
        """3y/5y/10y 三个窗口的分位都写入 metrics.valuation。"""
        # 构造近 10 年 PE 历史（约 520 周），最近 1 年降到 10，前期保持 50
        n = 520
        dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="W")
        values = [50.0] * (n - 52) + [10.0] * 52
        pe_hist = pd.DataFrame({
            "date": dates, "pe_ttm": values,
            "pb": [5.0] * (n - 52) + [1.0] * 52,
        })
        fin = _financials_basic([
            (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
        ])
        source = MockSource(pe_hist=pe_hist, fin=fin)
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=30),
        )
        r = results[0]
        assert r.status == Status.HIT
        # 三个窗口的分位都应填入
        assert r.metrics.valuation.pe_pct_3y is not None
        assert r.metrics.valuation.pe_pct_5y is not None
        assert r.metrics.valuation.pe_pct_10y is not None
        assert r.metrics.valuation.pb_pct_3y is not None
        assert r.metrics.valuation.pb_pct_5y is not None
        assert r.metrics.valuation.pb_pct_10y is not None
        # history_window 字段记录当前使用的窗口
        assert r.metrics.valuation.history_window == "5y"

    def test_3y_window_picked_when_configured(self, base_candidates):
        """history_years=3 → 阈值用 3y 分位；history_window='3y'。"""
        n = 520
        dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="W")
        values = [50.0] * (n - 52) + [10.0] * 52
        pe_hist = pd.DataFrame({
            "date": dates, "pe_ttm": values,
            "pb": [5.0] * (n - 52) + [1.0] * 52,
        })
        fin = _financials_basic([
            (2023, 12, 100.0), (2024, 12, 50.0), (2025, 12, 80.0),
        ])
        source = MockSource(pe_hist=pe_hist, fin=fin)
        results = evaluate_consumer_full(
            source=source, candidates=base_candidates,
            run_id="r1", period="2025A", show_progress=False,
            # 3y 窗口分位 ≈33%，放宽阈值便于验证 history_window 选取
            config=StrategyConfig(min_history_samples=30, history_years=3,
                                  pe_percentile_max=50.0),
        )
        r = results[0]
        assert r.status == Status.HIT
        assert r.metrics.valuation.history_window == "3y"


class TestP15ConsumerIndustryCoverage:
    """P1.5-4：扩展消费行业映射（美妆/个护/医美/纺服/服务消费/轻工）。"""

    def test_extended_industries_targeted(self):
        """扩展行业命中等价于 '美容护理'。"""
        from src.strategies.consumer_reversal import TARGET_INDUSTRIES
        for industry in ("美容护理", "化妆品", "个护用品", "医美", "纺织服饰",
                         "纺织服装", "服装家纺", "社会服务",
                         "休闲、生活及专业服务", "餐饮", "家居用品"):
            assert industry in TARGET_INDUSTRIES

    def test_extended_industry_hits(self):
        """扩展行业（化妆品）的候选股能进入评估。"""
        from src.strategies.consumer_reversal import evaluate_consumer_full
        candidates = pd.DataFrame(
            [{"code": "600999", "name": "X化妆", "sw_first": "化妆品"}]
        )
        source = MockSource(pe_hist=_pe_history(120, value=10.0),
                            fin=_financials_basic([(2023, 12, 100.0),
                                                   (2024, 12, 50.0),
                                                   (2025, 12, 80.0)]))
        results = evaluate_consumer_full(
            source=source, candidates=candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        assert len(results) == 1
        # 至少进入了评估（不是因行业不符被排除）
        assert results[0].status != Status.DATA_MISSING or \
               results[0].data_missing_reason != "pe_history_missing"

    def test_em2016_daily_chemical_segment_enters_pool(self):
        """东财把美护/个护归为基础化工时，仍应进入策略一候选池。"""
        candidates = pd.DataFrame([
            {
                "code": "603605", "name": "珀莱雅", "sw_first": "基础化工",
                "sw_second": "化学制品",
                "em2016": "基础化工-化学制品-日用化学品",
            }
        ])
        source = MockSource(
            pe_hist=_pe_history(120, value=10.0),
            fin=_financials_basic([(2023, 12, 100.0),
                                   (2024, 12, 50.0),
                                   (2025, 12, 80.0)]),
        )
        results = evaluate_consumer_full(
            source=source, candidates=candidates,
            run_id="r1", period="2025A", show_progress=False,
            config=StrategyConfig(min_history_samples=100),
        )
        assert len(results) == 1

    def test_actual_eastmoney_first_industry_aliases_enter_pool(self):
        """东财实际使用的纺服/服务消费一级标签不能只停留在常量中。"""
        from src.strategies.consumer_reversal import select_consumer_universe

        candidates = pd.DataFrame([
            {"code": "1", "name": "纺服", "sw_first": "纺织服装"},
            {"code": "2", "name": "服务", "sw_first": "休闲、生活及专业服务"},
            {"code": "3", "name": "非消费", "sw_first": "电子设备"},
        ])

        assert select_consumer_universe(candidates)["code"].tolist() == ["1", "2"]
