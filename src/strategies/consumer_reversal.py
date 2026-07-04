"""策略一：消费股反转。

逻辑：
- 行业：申万/EM2016 一级 食品饮料 / 家用电器(或家电) / 美容护理 / 商贸零售
- 估值：当前 PE-TTM 处于近 5 年 30% 分位以下
- 业绩：最近一期财报扣非净利润同比增速 > 30%
- 反转（可选）：满足「业绩拐点」或「趋势验证」任一
  - 业绩拐点：当期扣非 TTM 同比 ≥ 30% AND 上一期 < 0%（刚从负转正）
  - 趋势验证：当期 ≥ 20% AND 上一期 ≥ 20% AND 上上期 < 0%（连续改善 + 历史低点）

输入：股票池 + DataSource（拉估值历史和财务摘要）
输出：
- run_consumer_reversal: 旧 CSV 入口（仅 hit），向后兼容
- evaluate_consumer_full: 新状态化入口（每只候选股都有 ScreeningResult）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd
from tqdm import tqdm

from ..collectors.base import DataSource
from ..indicators.growth import compute_deducted_ttm, compute_yoy_growth
from ..indicators.valuation import compute_pe_pb_percentile
from ..screening import MetricsSchema, ScreeningResult, Status


# 策略一目标行业（同时兼容申万 + EM2016 + 新浪 行业名）
# P1.5-4：扩展消费行业映射，覆盖美妆/个护/医美/鞋服/餐饮/旅游/教育等
TARGET_INDUSTRIES = [
    "食品饮料",
    "家用电器", "家电",  # 申万 vs EM2016
    "美容护理", "化妆品", "个护用品", "医美", "医疗美容",  # P1.5-4 美容护理细分
    "商贸零售", "商业百货", "零售", "百货",  # 兼容多种命名
    "纺织服饰", "服装家纺", "服装", "鞋帽",  # P1.5-4 纺服
    "社会服务", "餐饮", "酒店餐饮", "旅游", "旅游零售",  # P1.5-4 服务消费
    "轻工制造", "文教用品", "家居", "家居用品",  # P1.5-4 轻工消费
]

# 默认阈值
DEFAULT_PE_PERCENTILE_MAX = 30.0
DEFAULT_DEDUCTED_YOY_MIN = 0.30  # 30%

# 反转判定阈值
INFLECTION_CURRENT_MIN = 0.30   # 业绩拐点：当期 yoy ≥ 30%
INFLECTION_PREV_MAX = 0.0       #           前期 yoy < 0%
TREND_CURRENT_MIN = 0.20        # 趋势验证：当期 yoy ≥ 20%
TREND_PREV_MIN = 0.20           #           前期 yoy ≥ 20%
TREND_PREV_PREV_MAX = 0.0       #           前前期 yoy < 0%

# P1-1 新增默认阈值
DEFAULT_PB_PERCENTILE_MAX = 50.0  # PB 5 年分位 < 50%
DEFAULT_REVENUE_YOY_MIN = 0.10    # 营收同比 ≥ 10%（避免扣非高增但营收停滞）
DEFAULT_GROSS_MARGIN_IMPROVEMENT = -0.005  # 毛利率同比下降 ≤ 0.5%（容忍小幅下降）
DEFAULT_OCF_PER_SHARE_MIN = 0.0  # 每股经营现金流必须为正
DEFAULT_DEDUCTED_TO_PARENT_MIN = 0.60  # 扣非净利 / 归母净利过低，说明利润质量存疑

# P1.5-4：PE/PB 分位时间窗口支持 3y / 5y / 10y
SUPPORTED_HISTORY_WINDOWS = (3, 5, 10)


def is_inflection(yoy_series: list[float]) -> bool:
    """业绩拐点判定：当期 yoy ≥ 30% AND 上一期 yoy < 0%（刚从负转正）。

    需要至少 2 期数据。
    """
    if len(yoy_series) < 2:
        return False
    current, prev = yoy_series[-1], yoy_series[-2]
    if pd.isna(current) or pd.isna(prev):
        return False
    return current >= INFLECTION_CURRENT_MIN and prev < INFLECTION_PREV_MAX


def is_trend(yoy_series: list[float]) -> bool:
    """趋势验证判定：当期 ≥ 20% AND 前期 ≥ 20% AND 前前期 < 0%（连续改善 + 历史低点）。

    需要至少 3 期数据。比拐点选更宽，避免错过则报后第二期才抓。
    """
    if len(yoy_series) < 3:
        return False
    current, prev, prev_prev = yoy_series[-1], yoy_series[-2], yoy_series[-3]
    if any(pd.isna(x) for x in (current, prev, prev_prev)):
        return False
    return (
        current >= TREND_CURRENT_MIN
        and prev >= TREND_PREV_MIN
        and prev_prev < TREND_PREV_PREV_MAX
    )


def _to_float(value) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class StrategyConfig:
    pe_percentile_max: float = DEFAULT_PE_PERCENTILE_MAX
    deducted_yoy_min: float = DEFAULT_DEDUCTED_YOY_MIN
    # P1.5-4：history_years 支持参数化（3/5/10），并校验非法值
    history_years: int = 5
    min_history_samples: int = 100  # PE 历史样本至少这么多
    require_reversal_check: bool = True  # 开启反转判定（拐点 OR 趋势）
    # P1-1 新增信号开关 + 阈值
    require_pb_percentile: bool = True
    pb_percentile_max: float = DEFAULT_PB_PERCENTILE_MAX
    require_revenue_confirmation: bool = True
    revenue_yoy_min: float = DEFAULT_REVENUE_YOY_MIN
    require_gross_margin_improvement: bool = True
    # 毛利率同比变化下限：-0.005 表示允许下降 0.5pp；正值要求上升
    gross_margin_yoy_min: float = DEFAULT_GROSS_MARGIN_IMPROVEMENT
    # P1：策略一经营质量过滤。缺失时 watch，明确不达标时 rejected。
    require_ocf_per_share_positive: bool = True
    ocf_per_share_min: float = DEFAULT_OCF_PER_SHARE_MIN
    require_deducted_profit_quality: bool = True
    deducted_to_parent_min: float = DEFAULT_DEDUCTED_TO_PARENT_MIN

    def __post_init__(self) -> None:
        # P1.5-4：history_years 必须在支持窗口内
        if self.history_years not in SUPPORTED_HISTORY_WINDOWS:
            raise ValueError(
                f"history_years 必须是 {SUPPORTED_HISTORY_WINDOWS} 之一，"
                f"得到 {self.history_years!r}"
            )


def run_consumer_reversal(
    source: DataSource,
    candidates: pd.DataFrame,
    config: StrategyConfig | None = None,
    show_progress: bool = True,
) -> pd.DataFrame:
    """跑策略一筛选（旧 CSV 入口，仅返回 hit 清单，向后兼容）。

    Args:
        source: 数据源
        candidates: 候选股票池（至少含 code, name, sw_first）
        config: 策略参数
        show_progress: 是否显示进度条
    """
    config = config or StrategyConfig()
    results = evaluate_consumer_full(
        source=source, candidates=candidates, run_id="legacy",
        period="", config=config, show_progress=show_progress,
    )
    hit_rows = []
    for r in results:
        if r.status == Status.HIT and r.metrics:
            hit_rows.append(_metrics_to_csv_row(r))
    if not hit_rows:
        return pd.DataFrame()
    df = pd.DataFrame(hit_rows)
    return df.sort_values("deducted_yoy_growth", ascending=False).reset_index(drop=True)


def evaluate_consumer_full(
    *,
    source: DataSource,
    candidates: pd.DataFrame,
    run_id: str,
    period: str,
    config: Optional[StrategyConfig] = None,
    show_progress: bool = True,
) -> list[ScreeningResult]:
    """状态化入口：每只进入策略一评估的股票都返回一个 ScreeningResult。

    Args:
        source: 数据源
        candidates: 候选股票池（含 code, name, sw_first）
        run_id: 关联 screen_runs.run_id
        period: 报告期，如 "2025A"
        config: 策略参数
        show_progress: 是否显示进度条

    Returns:
        list[ScreeningResult]：行业粗筛后的每只股票一个结果
    """
    config = config or StrategyConfig()
    if candidates.empty:
        return []

    pool = candidates[candidates["sw_first"].isin(TARGET_INDUSTRIES)].copy()
    if pool.empty:
        return []

    out: list[ScreeningResult] = []
    iterator = zip(pool["code"], pool["name"], pool["sw_first"])
    if show_progress:
        iterator = tqdm(list(iterator), desc="策略一筛选", ncols=80)

    for code, name, sw_first in iterator:
        result = _evaluate_one_to_result(
            source=source,
            code=str(code), name=str(name), sw_first=str(sw_first),
            config=config, run_id=run_id, period=period,
        )
        out.append(result)
    return out


def _evaluate_one_to_result(
    *,
    source: DataSource,
    code: str,
    name: str,
    sw_first: str,
    config: StrategyConfig,
    run_id: str,
    period: str,
) -> ScreeningResult:
    """单股评估，返回带 status + reason + metrics 的 ScreeningResult。

    失败语义：
    - PE 历史拉不到 / 样本不足 → data_missing, pe_history_missing
    - 财务表为空 / 缺 deducted_net_profit 列 → data_missing, deducted_profit_missing
    - 扣非 TTM 算不出 → data_missing, deducted_profit_missing
    - PE 分位 > 阈值 → rejected, pe_percentile_too_high
    - 扣非同比 < 阈值 → rejected, deducted_yoy_too_low
    - 反转判定未通过 → rejected, not_inflection_or_trend
    - P1-1：PB 分位 > 阈值 → rejected, pb_percentile_too_high
    - P1-1：营收同比 < 阈值 → rejected, revenue_yoy_too_low
    - P1-1：毛利率恶化（同比变化 < gross_margin_yoy_min）→ rejected, gross_margin_deteriorating
    - P1：每股经营现金流 ≤ 阈值 → rejected, cashflow_quality_failed
    - P1：扣非净利/归母净利过低 → rejected, deducted_profit_quality_failed
    - 全部通过 → hit, all_thresholds_met
    - 代码异常 → error, 异常 message

    P1-1 设计取舍：
    - PB 分位、营收同比、毛利率都是 P1-1 信号；缺失时进入 watch（不直接 rejected）
    - 阈值不达标时进入 rejected（硬过滤）
    - 这样保留旧候选股（数据缺失时降级 watch），但阈值不达标时仍然剔除
    """
    metrics = MetricsSchema()
    metrics.source_status.financials = "ok"
    metrics.source_status.valuation = "ok"
    common = dict(run_id=run_id, code=code, name=name,
                  strategy="consumer", period=period)

    try:
        # 1. PE 历史分位（P1.5-4：始终拉 10 年，再按窗口算分位）
        try:
            pe_hist = source.get_pe_pb_history(code, years=10)
        except Exception:
            metrics.source_status.valuation = "error"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="pe_history_missing", metrics=metrics
            )

        # P1.5-4：3y/5y/10y 全部算出来，写入对应字段；当前阈值用 config.history_years
        pe_stats = {
            w: compute_pe_pb_percentile(pe_hist, "pe_ttm", w)
            for w in SUPPORTED_HISTORY_WINDOWS
        }
        pe_stat = pe_stats[config.history_years]
        if (
            pe_stat["percentile"] is None
            or pe_stat["sample_count"] < config.min_history_samples
        ):
            metrics.source_status.valuation = "missing"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="pe_history_missing", metrics=metrics
            )

        # 2. 扣非 TTM 增速
        try:
            fin = source.get_financial_abstract(code)
        except Exception:
            metrics.source_status.financials = "error"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="deducted_profit_missing", metrics=metrics
            )

        if fin.empty or "deducted_net_profit" not in fin.columns:
            metrics.source_status.financials = "missing"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="deducted_profit_missing", metrics=metrics
            )

        fin_ttm = compute_deducted_ttm(fin)
        fin_growth = compute_yoy_growth(fin_ttm, "deducted_ttm")
        if fin_growth.empty:
            metrics.source_status.financials = "missing"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="deducted_profit_missing", metrics=metrics
            )

        latest = fin_growth.sort_values("report_date").iloc[-1]
        yoy = latest.get("deducted_ttm_yoy_growth")
        if pd.isna(yoy):
            metrics.source_status.financials = "missing"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="deducted_profit_missing", metrics=metrics
            )

        # 3. 反转判定（拐点 / 趋势）
        fin_growth_sorted = fin_growth.sort_values("report_date").reset_index(drop=True)
        yoy_series = fin_growth_sorted["deducted_ttm_yoy_growth"].dropna().tolist()
        inflection = is_inflection(yoy_series)
        trend = is_trend(yoy_series)

        # 4. P1-1 收集辅助指标（best-effort，缺失不影响硬过滤）
        # P1.5-4：PB 历史分位同样多窗口
        pb_stats = {
            w: compute_pe_pb_percentile(pe_hist, "pb", w)
            for w in SUPPORTED_HISTORY_WINDOWS
        }
        pb_stat = pb_stats[config.history_years]
        # 营收同比：AkShare stock_financial_abstract 的"营业总收入增长率"是百分数
        # （如 15.66 = 15.66%），统一除以 100 转小数。
        revenue_yoy = latest.get("revenue_yoy")
        revenue_yoy_f: Optional[float] = None
        if pd.notna(revenue_yoy):
            try:
                revenue_yoy_f = float(revenue_yoy) / 100.0
            except (ValueError, TypeError):
                revenue_yoy_f = None
        # 毛利率同比变化：gross_margin 也是百分数（91.96 = 91.96%），
        # 转小数后相减，结果单位为小数（-0.005 = -0.5pp），跟 gross_margin_yoy_min 对齐。
        gross_margin_now_raw = latest.get("gross_margin")
        gross_margin_now: Optional[float] = (
            float(gross_margin_now_raw) / 100.0
            if pd.notna(gross_margin_now_raw) else None
        )
        gross_margin_yoy_change: Optional[float] = None
        if gross_margin_now is not None:
            latest_report_date = pd.to_datetime(latest.get("report_date"))
            prev_year = latest_report_date.year - 1
            prev_row = fin_growth_sorted[
                (pd.to_datetime(fin_growth_sorted["report_date"]).dt.year == prev_year)
                & (pd.to_datetime(fin_growth_sorted["report_date"]).dt.month == 12)
            ]
            if not prev_row.empty:
                prev_gm_raw = prev_row.iloc[-1].get("gross_margin")
                if pd.notna(prev_gm_raw):
                    gross_margin_yoy_change = (
                        gross_margin_now - float(prev_gm_raw) / 100.0
                    )
        ocf_per_share = _to_float(latest.get("ocf_per_share"))
        net_profit_base = _to_float(latest.get("net_profit_attr_parent"))
        if net_profit_base is None:
            net_profit_base = _to_float(latest.get("net_profit"))
        deducted_profit = _to_float(latest.get("deducted_net_profit"))
        deducted_to_parent: Optional[float] = None
        if (
            deducted_profit is not None
            and net_profit_base is not None
            and net_profit_base > 0
        ):
            deducted_to_parent = deducted_profit / net_profit_base

        # 5. 填充 metrics
        # P1.5-4：所有窗口的分位都写入；阈值用 config.history_years 选的窗口
        metrics.valuation.pe_ttm = float(pe_stat["current"]) if pd.notna(pe_stat["current"]) else None
        metrics.valuation.pe_pct_3y = float(pe_stats[3]["percentile"]) if pe_stats[3]["percentile"] is not None else None
        metrics.valuation.pe_pct_5y = float(pe_stats[5]["percentile"]) if pe_stats[5]["percentile"] is not None else None
        metrics.valuation.pe_pct_10y = float(pe_stats[10]["percentile"]) if pe_stats[10]["percentile"] is not None else None
        metrics.valuation.history_window = f"{config.history_years}y"
        metrics.valuation.pb = float(pb_stat["current"]) if pb_stat["current"] is not None else None
        metrics.valuation.pb_pct_3y = float(pb_stats[3]["percentile"]) if pb_stats[3]["percentile"] is not None else None
        metrics.valuation.pb_pct_5y = float(pb_stats[5]["percentile"]) if pb_stats[5]["percentile"] is not None else None
        metrics.valuation.pb_pct_10y = float(pb_stats[10]["percentile"]) if pb_stats[10]["percentile"] is not None else None
        metrics.growth.deducted_profit_yoy_ttm = float(yoy)
        metrics.growth.revenue_yoy = revenue_yoy_f
        metrics.quality.gross_margin = gross_margin_now
        if ocf_per_share is not None:
            metrics.source_status.extra["ocf_per_share"] = f"{ocf_per_share:.6f}"
        if deducted_to_parent is not None:
            metrics.source_status.extra["deducted_to_parent_profit"] = f"{deducted_to_parent:.6f}"

        # 6. 应用硬阈值，返回对应状态
        if pe_stat["percentile"] > config.pe_percentile_max:
            return ScreeningResult.rejected(
                **common, reject_reason="pe_percentile_too_high", metrics=metrics
            )
        if float(yoy) < config.deducted_yoy_min:
            return ScreeningResult.rejected(
                **common, reject_reason="deducted_yoy_too_low", metrics=metrics
            )
        if config.require_reversal_check and not (inflection or trend):
            return ScreeningResult.rejected(
                **common, reject_reason="not_inflection_or_trend", metrics=metrics
            )

        # P1-1：PB 分位
        if config.require_pb_percentile and pb_stat["percentile"] is not None:
            if pb_stat["percentile"] > config.pb_percentile_max:
                return ScreeningResult.rejected(
                    **common, reject_reason="pb_percentile_too_high", metrics=metrics
                )

        # P1-1：营收同比验证
        if config.require_revenue_confirmation:
            if revenue_yoy_f is None:
                # 数据缺失 → watch（不直接剔除，避免误杀）
                return ScreeningResult.watch(
                    **common, watch_reason="data_warning", metrics=metrics
                )
            if revenue_yoy_f < config.revenue_yoy_min:
                return ScreeningResult.rejected(
                    **common, reject_reason="revenue_yoy_too_low", metrics=metrics
                )

        # P1-1：毛利率改善
        if config.require_gross_margin_improvement:
            if gross_margin_yoy_change is None:
                return ScreeningResult.watch(
                    **common, watch_reason="data_warning", metrics=metrics
                )
            if gross_margin_yoy_change < config.gross_margin_yoy_min:
                return ScreeningResult.rejected(
                    **common, reject_reason="gross_margin_deteriorating", metrics=metrics
                )

        # P1：经营质量过滤。缺失降级 watch，明确不达标 rejected。
        if config.require_ocf_per_share_positive:
            if ocf_per_share is None:
                return ScreeningResult.watch(
                    **common, watch_reason="data_warning", metrics=metrics
                )
            if ocf_per_share <= config.ocf_per_share_min:
                return ScreeningResult.rejected(
                    **common, reject_reason="cashflow_quality_failed", metrics=metrics
                )

        if config.require_deducted_profit_quality:
            if deducted_to_parent is None:
                return ScreeningResult.watch(
                    **common, watch_reason="data_warning", metrics=metrics
                )
            if deducted_to_parent < config.deducted_to_parent_min:
                return ScreeningResult.rejected(
                    **common,
                    reject_reason="deducted_profit_quality_failed",
                    metrics=metrics,
                )

        return ScreeningResult.hit(
            **common, hit_reason="all_thresholds_met", metrics=metrics
        )

    except Exception as e:
        return ScreeningResult.from_exception(
            **common, error=f"{type(e).__name__}: {e}", metrics=metrics
        )


def _metrics_to_csv_row(result: ScreeningResult) -> dict:
    """把 hit 的 ScreeningResult 转回旧 CSV 行格式（保持向后兼容）。

    用于 run_consumer_reversal 旧入口输出。
    P1.5-4：pe_percentile 根据 history_window 选择对应窗口的分位。
    """
    m = result.metrics
    # P1.5-4：按 history_window 选 pe_percentile，默认 5y
    window = m.valuation.history_window or "5y"
    pe_pct_by_window = {
        "3y": m.valuation.pe_pct_3y,
        "5y": m.valuation.pe_pct_5y,
        "10y": m.valuation.pe_pct_10y,
    }
    pb_pct_by_window = {
        "3y": m.valuation.pb_pct_3y,
        "5y": m.valuation.pb_pct_5y,
        "10y": m.valuation.pb_pct_10y,
    }
    return {
        "code": result.code,
        "name": result.name,
        "sw_first": "",  # 旧 CSV 包含 sw_first，状态化后已不强制
        "report_date": None,
        "pe_ttm_current": m.valuation.pe_ttm,
        "pe_percentile": pe_pct_by_window.get(window),
        "pe_pct_3y": m.valuation.pe_pct_3y,
        "pe_pct_5y": m.valuation.pe_pct_5y,
        "pe_pct_10y": m.valuation.pe_pct_10y,
        "pb": m.valuation.pb,
        "pb_pct_3y": m.valuation.pb_pct_3y,
        "pb_pct_5y": m.valuation.pb_pct_5y,
        "pb_pct_10y": m.valuation.pb_pct_10y,
        "history_window": window,
        "pe_min": None,
        "pe_median": None,
        "pe_max": None,
        "pe_sample_count": None,
        "deducted_yoy_growth": m.growth.deducted_profit_yoy_ttm,
        "revenue_yoy": m.growth.revenue_yoy,
        "prev_yoy": None,
        "prev_prev_yoy": None,
        "is_inflection": False,
        "is_trend": False,
        "revenue": None,
        "gross_margin": m.quality.gross_margin,
        "ocf_per_share": m.source_status.extra.get("ocf_per_share"),
        "deducted_to_parent_profit": m.source_status.extra.get("deducted_to_parent_profit"),
    }


# === 向后兼容旧接口（保留外部脚本调用） ===
def _evaluate_one(
    source: DataSource,
    code: str,
    name: str,
    sw_first: str,
    config: StrategyConfig,
) -> dict | None:
    """旧接口：仅返回 hit 行 dict，无状态。供 run_phase2_strategy1.py 兼容使用。"""
    result = _evaluate_one_to_result(
        source=source, code=code, name=name, sw_first=sw_first,
        config=config, run_id="legacy", period="",
    )
    if result.status != Status.HIT:
        return None
    return _metrics_to_csv_row(result)
