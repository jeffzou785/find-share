"""策略三：出海隐形冠军。

逻辑：
- 行业：EM2016/申万一级 机械设备 / 交运设备(汽车) / 基础化工
- 海外业务：最新年报境外收入占比 > 30%
- 海外增速：境外收入同比 > 40%（有连续 2 年数据时强制校验；单年数据保留但降低置信度）
- 估值：当前 PE-TTM < 25
- 一致预期增速（可选）：东财研报 EPS Y1/Y2 增速 ≥ 15%
- 现金流质量（可选）：经营性现金流净额 / 净利润 ≥ 0.7
- 资产负债率（可选）：latest_year 总负债/总资产 < 60%

数据依赖：
- stock_industry 表（行业映射）
- overseas_revenue 表（年报解析结果）
- pe_pb_history 表 + indicators.valuation 算 PE
- financials 表（AkShare 拉营收/净利润）
- financials_full 表（新浪拉 EPS/三表细粒度）
- broker_reports 表（东财研报 EPS 预测）

输出：
- run_overseas_champion: 旧 CSV 入口（仅 hit），向后兼容
- evaluate_overseas_full: 新状态化入口（每只候选股都有 ScreeningResult）
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from tqdm import tqdm

from ..collectors.base import DataSource
from ..collectors.neglect_evidence import NeglectEvidenceCollector
from ..collectors.sina_impl import SinaFinancialSource
from ..indicators.valuation import compute_pe_pb_percentile
from ..screening import MetricsSchema, ScreeningResult, Status
from ..screening.period import parse_period, require_overseas_filter
from ..storage import DuckDBStore


# 策略三目标行业（EM2016 一级 + 申万兼容名）
TARGET_INDUSTRIES = [
    "机械设备",
    "交运设备",  # EM2016 用这个
    "汽车",  # 申万
    "基础化工",
    "化工",  # 兼容
]

DEFAULT_OVERSEAS_RATIO_MIN = 0.30  # 30%
DEFAULT_OVERSEAS_RATIO_MAX = 0.95  # 海外占比 >95% 视为解析异常
DEFAULT_OVERSEAS_YOY_MIN = 0.40  # 40%
DEFAULT_PE_TTM_MAX = 25.0

# 同比异常阈值（用于 parse_warning 判定）
OVERSEAS_YOY_ABS_MAX = 5.0     # |yoy| > 5 视为单位识别错
OVERSEAS_YOY_DROP_MIN = -0.80  # yoy < -80% 视为单位识别错

# 扩展条件阈值
DEFAULT_CONSENSUS_GROWTH_MIN = 0.15  # 一致预期 EPS 增速 ≥ 15%
DEFAULT_CASHFLOW_QUALITY_MIN = 0.70  # 经营现金流 / 净利润 ≥ 0.7
DEFAULT_DEBT_RATIO_MAX = 0.60        # 资产负债率 < 60%
DEFAULT_CONSENSUS_RECENT_DAYS = 90   # 一致预期取近 N 天研报


@dataclass
class StrategyConfig:
    overseas_ratio_min: float = DEFAULT_OVERSEAS_RATIO_MIN
    overseas_ratio_max: float = DEFAULT_OVERSEAS_RATIO_MAX
    overseas_yoy_min: float = DEFAULT_OVERSEAS_YOY_MIN
    pe_ttm_max: float = DEFAULT_PE_TTM_MAX
    require_overseas_yoy: bool = False  # 显式要求同比数据；缺失时 watch，不硬拒绝
    enforce_overseas_yoy_when_available: bool = True  # 有连续 2 年数据时默认校验海外收入同比
    sanity_check_yoy: bool = True  # 用同比做数据合理性过滤（|yoy|>5 或 <-80% 视为单位识别错）
    # 扩展条件开关
    # 一致预期默认关：需先跑 scripts/import_research_reports.py 拉研报，否则全市场命中 0
    require_consensus_growth: bool = False
    require_cashflow_quality: bool = True
    require_leverage: bool = True
    consensus_growth_min: float = DEFAULT_CONSENSUS_GROWTH_MIN
    cashflow_quality_min: float = DEFAULT_CASHFLOW_QUALITY_MIN
    debt_ratio_max: float = DEFAULT_DEBT_RATIO_MAX
    consensus_recent_days: int = DEFAULT_CONSENSUS_RECENT_DAYS


def is_yoy_anomaly(yoy: Optional[float]) -> bool:
    """True 如果海外收入同比异常（疑似单位识别错）。"""
    if yoy is None or pd.isna(yoy):
        return False
    return abs(yoy) > OVERSEAS_YOY_ABS_MAX or yoy < OVERSEAS_YOY_DROP_MIN


def _compute_overseas_yoy(
    yearly: dict[int, float],
    latest_year: Optional[int],
    overseas_revenue: Optional[float],
) -> Optional[float]:
    """用最新年和上一年境外收入计算同比；没有连续上一年时返回 None。"""
    if latest_year is None or overseas_revenue is None:
        return None
    prev = yearly.get(latest_year - 1)
    if prev is None or prev <= 0:
        return None
    return (overseas_revenue - prev) / prev


# === P1-2 被忽视证据 ===
# 当 reports_count_90d <= 此阈值时，认为研报覆盖不足（被忽视证据之一）
NEGLECT_LOW_REPORT_THRESHOLD = 3
# 默认统计窗口
DEFAULT_REPORT_LOOKBACK_DAYS = 90

# P1.5-2：候选 ratio 校验合理区间（best 候选 ratio 不在此区间时尝试 candidates_json）
RATIO_PLAUSIBLE_MIN = 0.05
RATIO_PLAUSIBLE_MAX = 0.95


def _parse_candidates_json(s: Optional[str]) -> list[dict]:
    """P1.5-2：解析 overseas_revenue.candidates_json，返回候选 dict 列表。

    容错：JSON 解析失败 / 字段缺失 → 返回空列表。
    """
    if not s:
        return []
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return []


def _pick_plausible_candidate(
    candidates: list[dict],
    total_revenue: float,
    *,
    ratio_min: float = RATIO_PLAUSIBLE_MIN,
    ratio_max: float = RATIO_PLAUSIBLE_MAX,
) -> tuple[Optional[float], Optional[str]]:
    """P1.5-2：从候选中挑出 ratio 最合理的境外收入（策略层 ratio 校验）。

    选择规则：
    1. 过滤 is_total_row=True 的候选（明显是总营收）
    2. 计算每个候选的 ratio = revenue_yuan / total_revenue
    3. 过滤 ratio 在 [ratio_min, ratio_max] 内
    4. 在剩余候选中按 confidence（high>medium>low）和金额排序，选最佳

    Returns:
        (overseas_revenue_yuan, parse_warning)
        - 若无可信候选：返回 (None, "no_plausible_candidate_in_json")
        - 若找到候选：返回 (revenue_yuan, "candidate_chose_from_json:...")
    """
    if not candidates or total_revenue <= 0:
        return None, None

    non_total = [c for c in candidates if not c.get("is_total_row", False)]
    if not non_total:
        return None, "all_candidates_are_total_row"

    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    plausible = []
    for c in non_total:
        rev_yuan = c.get("revenue_yuan")
        if rev_yuan is None or rev_yuan <= 0:
            continue
        ratio = float(rev_yuan) / total_revenue
        if ratio_min <= ratio <= ratio_max:
            plausible.append((c, ratio))

    if not plausible:
        return None, "no_plausible_candidate_in_json"

    # 按 confidence desc, 与 0.5 距离 asc（最"典型"的境外占比）
    plausible.sort(
        key=lambda x: (
            -confidence_rank.get(x[0].get("confidence", "medium"), 2),
            abs(x[1] - 0.5),
        )
    )
    best_c, best_ratio = plausible[0]
    warn = (
        f"candidate_chose_from_json:region={best_c.get('region_name')}"
        f",revenue_yi={float(best_c.get('revenue_yuan', 0))/1e8:.2f}"
        f",ratio={best_ratio:.2f}"
    )
    return float(best_c.get("revenue_yuan")), warn


def _compute_reports_coverage(
    store: DuckDBStore,
    code: str,
    lookback_days: int = DEFAULT_REPORT_LOOKBACK_DAYS,
) -> int:
    """P1-2：近 N 天该股票的研报数（被忽视证据之一）。

    数据来自 broker_reports 表（需先跑 import_research_reports.py）。
    """
    try:
        df = store.load_broker_reports(code=code)
    except Exception:
        return 0
    if df.empty or "publish_date" not in df.columns:
        return 0
    df = df.copy()
    df["publish_date"] = pd.to_datetime(df["publish_date"], errors="coerce")
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=lookback_days)
    return int((df["publish_date"] >= cutoff).sum())


def evaluate_overseas_full(
    *,
    source: DataSource,
    store: DuckDBStore,
    candidates: pd.DataFrame,
    run_id: str,
    period: str,
    config: Optional[StrategyConfig] = None,
    show_progress: bool = True,
    sina_source: Optional[SinaFinancialSource] = None,
    neglect_collector: Optional[NeglectEvidenceCollector] = None,
    enable_neglect_evidence: bool = False,
) -> list[ScreeningResult]:
    """状态化入口：每只进入策略三评估的股票都返回一个 ScreeningResult。

    Args:
        source: 数据源（AkShare，拉 PE 历史和财务摘要）
        store: DuckDB（读 overseas_revenue）
        candidates: 候选股票池（含 code, name, sw_first）
        run_id: 关联 screen_runs.run_id
        period: 报告期，如 "2025A"
        config: 策略参数
        sina_source: 可选的新浪源（不传则 lazy 实例化，失败降级）
        neglect_collector: P1.5-3 被忽视证据 collector。传 None 且 enable_neglect_evidence=True
            时 lazy 实例化。
        enable_neglect_evidence: 关闭则跳过新闻/概念查询（避免不必要的网络调用）。
    """
    config = config or StrategyConfig()
    if candidates.empty:
        return []

    pool = candidates[candidates["sw_first"].isin(TARGET_INDUSTRIES)].copy()
    if pool.empty:
        return []

    # P2-2：解析 period 决定海外收入过滤行为
    # 季报（Q1/Q3）没有完整分地区附注 → 不强求 overseas_revenue 数据
    period_info = parse_period(period)
    overseas_required = require_overseas_filter(period)  # 默认 True（含无法解析）

    # 加载 overseas_revenue，构造 overseas_map（含 P1-3 parse_warning 元数据）
    overseas_df = store.load_overseas_revenue()
    overseas_map: dict[str, dict[int, float]] = {}
    overseas_meta: dict[tuple[str, int], dict] = {}  # (code, year) → {parse_warning, confidence, candidates}
    if not overseas_df.empty:
        overseas_df = overseas_df.copy()
        overseas_df["revenue_yuan"] = (
            overseas_df["revenue"]
            * overseas_df["revenue_unit"].map(
                {"元": 1.0, "千元": 1_000.0, "万元": 10_000.0,
                 "百万": 1_000_000.0, "亿元": 100_000_000.0}
            ).fillna(1.0)
        )
        overseas_df.loc[overseas_df["revenue_yuan"] > 5e12, "revenue_yuan"] = (
            overseas_df.loc[overseas_df["revenue_yuan"] > 5e12, "revenue_yuan"] / 1e4
        )
        for _, row in overseas_df.iterrows():
            overseas_map.setdefault(row["stock_code"], {})[
                int(row["report_year"])
            ] = row["revenue_yuan"]
            # P1-3：保留 parse_warning / confidence 元数据；P1.5-2：保留 candidates_json
            overseas_meta[(row["stock_code"], int(row["report_year"]))] = {
                "parse_warning": row.get("parse_warning"),
                "confidence": row.get("confidence"),
                "candidates": _parse_candidates_json(row.get("candidates_json")),
            }

    if sina_source is None:
        try:
            sina_source = SinaFinancialSource()
        except Exception:
            sina_source = None

    # P1.5-3：被忽视证据 collector（默认关闭，按需开启）
    if enable_neglect_evidence and neglect_collector is None:
        try:
            neglect_collector = NeglectEvidenceCollector()
        except Exception:
            neglect_collector = None
    if not enable_neglect_evidence:
        neglect_collector = None

    out: list[ScreeningResult] = []
    iterator = zip(pool["code"], pool["name"], pool["sw_first"])
    if show_progress:
        iterator = tqdm(list(iterator), desc="策略三筛选", ncols=80)

    for code, name, sw_first in iterator:
        result = _evaluate_one_to_result(
            source=source, store=store, code=str(code), name=str(name),
            sw_first=str(sw_first), config=config,
            overseas_map=overseas_map, overseas_meta=overseas_meta,
            sina_source=sina_source,
            neglect_collector=neglect_collector,
            run_id=run_id, period=period,
            overseas_required=overseas_required,
        )
        out.append(result)
    return out


def _evaluate_one_to_result(
    *,
    source: DataSource,
    store: DuckDBStore,
    code: str,
    name: str,
    sw_first: str,
    config: StrategyConfig,
    overseas_map: dict[str, dict[int, float]],
    overseas_meta: dict[tuple[str, int], dict],
    sina_source: Optional[SinaFinancialSource],
    run_id: str,
    period: str,
    neglect_collector: Optional[NeglectEvidenceCollector] = None,
    overseas_required: bool = True,
) -> ScreeningResult:
    """单股评估，返回带 status + reason + metrics 的 ScreeningResult。

    P2-2：overseas_required=False（季报场景）时，没有 overseas_revenue 数据
    不再判 data_missing；改为以财务/估值/质量过滤为主，海外占比作为可选 context。
    """
    metrics = MetricsSchema()
    metrics.source_status.financials = "ok"
    metrics.source_status.valuation = "ok"
    metrics.source_status.overseas_parser = "ok"
    metrics.source_status.consensus = "skipped"
    common = dict(run_id=run_id, code=code, name=name,
                  strategy="overseas", period=period)

    try:
        # 1. overseas_revenue 数据
        # P2-2：季报场景下缺失不直接判 data_missing，仍允许走完财务/估值/质量
        yearly: dict[int, float] = overseas_map.get(code, {})
        raw_yearly = {
            int(year): float(revenue)
            for year, revenue in yearly.items()
            if revenue is not None and not pd.isna(revenue)
        }
        valid_yearly = {
            year: revenue for year, revenue in raw_yearly.items() if revenue > 0
        }
        overseas_year_count = len(valid_yearly)
        overseas_revenue: Optional[float] = None
        latest_year: Optional[int] = None
        overseas_yoy: Optional[float] = None
        overseas_ratio: Optional[float] = None
        ratio_parse_warning: Optional[str] = None

        metrics.source_status.extra["overseas_year_count"] = str(overseas_year_count)
        if raw_yearly:
            latest_year = max(raw_yearly.keys())
            overseas_revenue = raw_yearly[latest_year]

        if not overseas_revenue or overseas_revenue <= 0:
            if overseas_required:
                metrics.source_status.overseas_parser = "missing"
                metrics.source_status.extra["overseas_yoy_status"] = "missing"
                return ScreeningResult.data_missing(
                    **common, data_missing_reason="overseas_revenue_missing",
                    metrics=metrics,
                )
            # 季报：标记缺失但继续
            metrics.source_status.overseas_parser = "missing"
            metrics.source_status.extra["overseas_yoy_status"] = "missing"
            overseas_revenue = None
        else:
            # 计算 overseas_yoy（需要连续上一年）
            overseas_yoy = _compute_overseas_yoy(valid_yearly, latest_year, overseas_revenue)
            if overseas_yoy is not None:
                metrics.source_status.extra["overseas_yoy_status"] = "ok"
            elif overseas_year_count == 1:
                metrics.source_status.extra["overseas_yoy_status"] = "single_year"
            else:
                metrics.source_status.extra["overseas_yoy_status"] = "missing_prev_year"

        # 2. 拉营收（算海外占比）
        try:
            fin = source.get_financial_abstract(code)
        except Exception:
            metrics.source_status.financials = "error"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="financial_data_missing",
                metrics=metrics,
            )

        if fin.empty:
            metrics.source_status.financials = "missing"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="financial_data_missing",
                metrics=metrics,
            )

        fin_sorted = fin.sort_values("report_date") if "report_date" in fin.columns else fin

        # P2-2：季报场景无 latest_year（overseas 缺失）时，退到最新年报行
        if latest_year is not None:
            target_date = pd.Timestamp(year=latest_year, month=12, day=31)
            annual_rows = fin_sorted[
                pd.to_datetime(fin_sorted["report_date"]) == target_date
            ]
        else:
            annual_rows = pd.DataFrame()
        if annual_rows.empty:
            fin_sorted_copy = fin_sorted.copy()
            fin_sorted_copy["report_date"] = pd.to_datetime(fin_sorted_copy["report_date"])
            annual_rows = fin_sorted_copy[fin_sorted_copy["report_date"].dt.month == 12]
        if annual_rows.empty:
            metrics.source_status.financials = "missing"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="financial_data_missing",
                metrics=metrics,
            )

        latest_fin = annual_rows.iloc[-1]
        revenue = latest_fin.get("revenue")
        if pd.isna(revenue) or revenue <= 0:
            metrics.source_status.financials = "missing"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="financial_data_missing",
                metrics=metrics,
            )

        if overseas_revenue is not None and overseas_revenue > 0:
            overseas_ratio = overseas_revenue / revenue

            # P1.5-2：策略层 ratio 校验。当 best 候选 ratio 异常（>0.95 或 <0.05）时，
            # 尝试从 candidates_json 挑出 ratio 更合理的候选。
            if (
                latest_year is not None
                and (overseas_ratio >= RATIO_PLAUSIBLE_MAX
                     or overseas_ratio < RATIO_PLAUSIBLE_MIN)
            ):
                meta_for_ratio = overseas_meta.get((code, latest_year), {})
                cands = meta_for_ratio.get("candidates") or []
                alt_revenue, alt_warn = _pick_plausible_candidate(
                    cands, float(revenue),
                    ratio_min=RATIO_PLAUSIBLE_MIN,
                    ratio_max=RATIO_PLAUSIBLE_MAX,
                )
                if alt_revenue is not None and alt_revenue > 0:
                    overseas_revenue = alt_revenue
                    overseas_ratio = overseas_revenue / revenue
                    overseas_yoy = _compute_overseas_yoy(valid_yearly, latest_year, overseas_revenue)
                    if overseas_yoy is not None:
                        metrics.source_status.extra["overseas_yoy_status"] = "ok"
                    ratio_parse_warning = alt_warn or "candidate_chose_from_json"
                elif alt_warn:
                    # 候选都不合理 → 记录原因，保持原值，后续阈值检查会 reject
                    ratio_parse_warning = alt_warn

        # 3. PE 历史
        try:
            pe_hist = source.get_pe_pb_history(code, years=5)
        except Exception:
            metrics.source_status.valuation = "error"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="financial_data_missing",
                metrics=metrics,
            )

        if pe_hist.empty:
            metrics.source_status.valuation = "missing"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="financial_data_missing",
                metrics=metrics,
            )
        latest_pe_row = pe_hist.sort_values("date").iloc[-1]
        pe_ttm = latest_pe_row.get("pe_ttm")
        if pd.isna(pe_ttm) or pe_ttm <= 0:
            metrics.source_status.valuation = "missing"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="financial_data_missing",
                metrics=metrics,
            )

        pe_stat = compute_pe_pb_percentile(pe_hist, "pe_ttm", years=5)

        # 4. 扩展条件
        # P2-2：latest_year 可能为 None（季报+overseas 缺失），扩展条件优雅降级
        consensus_year = latest_year if latest_year is not None else int(pd.Timestamp.now().year) - 1
        consensus = _check_consensus_growth(sina_source, store, code, consensus_year, config)
        cashflow = _check_cashflow_quality(sina_source, store, code, consensus_year, latest_fin)
        leverage = _check_leverage(sina_source, code, consensus_year)

        # 5. 填充 metrics
        metrics.valuation.pe_ttm = float(pe_ttm)
        metrics.valuation.pe_pct_5y = pe_stat["percentile"]
        if overseas_ratio is not None:
            metrics.overseas.overseas_ratio = float(overseas_ratio)
        metrics.overseas.overseas_yoy = float(overseas_yoy) if overseas_yoy is not None else None
        if overseas_revenue is not None:
            metrics.overseas.overseas_revenue_yi = float(overseas_revenue) / 1e8
        metrics.overseas.total_revenue_yi = float(revenue) / 1e8
        if cashflow.get("ocf_to_profit") is not None:
            metrics.quality.ocf_to_net_profit = float(cashflow["ocf_to_profit"])
        # P1.5-6：补全 legacy CSV 绝对值字段
        if cashflow.get("ocf_net_yi") is not None:
            metrics.quality.ocf_net_yi = float(cashflow["ocf_net_yi"])
        if cashflow.get("net_profit_yi") is not None:
            metrics.quality.net_profit_yi = float(cashflow["net_profit_yi"])
        if leverage.get("debt_ratio") is not None:
            metrics.quality.debt_ratio = float(leverage["debt_ratio"])
        if leverage.get("total_liabilities_yi") is not None:
            metrics.quality.total_liabilities_yi = float(leverage["total_liabilities_yi"])
        if leverage.get("total_assets_yi") is not None:
            metrics.quality.total_assets_yi = float(leverage["total_assets_yi"])
        # P1.5-6：一致预期补全
        if consensus.get("eps_current") is not None:
            metrics.catalyst.eps_current = float(consensus["eps_current"])
        if consensus.get("eps_forecast_y1") is not None:
            metrics.catalyst.eps_forecast_y1 = float(consensus["eps_forecast_y1"])
        if consensus.get("eps_forecast_y2") is not None:
            metrics.catalyst.eps_forecast_y2 = float(consensus["eps_forecast_y2"])
        if consensus.get("eps_y1_growth") is not None:
            metrics.catalyst.eps_y1_growth = float(consensus["eps_y1_growth"])
        if consensus.get("eps_y2_growth") is not None:
            metrics.catalyst.eps_y2_growth = float(consensus["eps_y2_growth"])
        # P1-2：研报覆盖度（被忽视证据之一；不改硬过滤，仅填 metrics）
        metrics.catalyst.reports_count_90d = _compute_reports_coverage(
            store, code, DEFAULT_REPORT_LOOKBACK_DAYS
        )
        # P1.5-3：被忽视证据链（news / concept / hot / relative_return / neglect_evidence）
        if neglect_collector is not None:
            try:
                metrics.catalyst.news_count_30d = neglect_collector.get_news_count_30d(code)
            except Exception:
                pass
            try:
                metrics.catalyst.is_ai_related = neglect_collector.is_ai_related(code)
            except Exception:
                pass
            try:
                metrics.catalyst.hot_reason_count_30d = neglect_collector.get_hot_reason_count_30d(code)
            except Exception:
                pass
            try:
                metrics.catalyst.relative_return_60d = neglect_collector.get_relative_return_60d(code)
            except Exception:
                pass
            metrics.catalyst.neglect_evidence = neglect_collector.compute_neglect_evidence(
                reports_count_90d=metrics.catalyst.reports_count_90d,
                news_count_30d=metrics.catalyst.news_count_30d,
                is_ai_related=metrics.catalyst.is_ai_related,
                hot_reason_count_30d=metrics.catalyst.hot_reason_count_30d,
                relative_return_60d=metrics.catalyst.relative_return_60d,
            )

        # 6. 同比异常 → watch（parse_warning），不直接 rejected
        if config.sanity_check_yoy and is_yoy_anomaly(overseas_yoy):
            metrics.overseas.parse_warning = (
                f"overseas_yoy_anomaly: {overseas_yoy:.2f}"
            )
            return ScreeningResult.watch(
                **common, watch_reason="parse_warning", metrics=metrics
            )

        # 6.5 P1-3 + P1.5-2：合并 parse_warning（仅在有 overseas 数据时）
        if latest_year is not None:
            meta = overseas_meta.get((code, latest_year), {})
            db_parse_warning = meta.get("parse_warning")
            confidence = meta.get("confidence")
            combined_warning_parts = [
                p for p in (db_parse_warning, ratio_parse_warning) if p
            ]
            combined_warning = "; ".join(combined_warning_parts) or None
            if combined_warning:
                metrics.overseas.parse_warning = combined_warning
                return ScreeningResult.watch(
                    **common, watch_reason="parse_warning", metrics=metrics
                )
            if confidence == "low":
                metrics.overseas.parse_warning = (
                    f"low_confidence_parser:region={latest_year}"
                )
                return ScreeningResult.watch(
                    **common, watch_reason="data_warning", metrics=metrics
                )

        # 7. 阈值检查
        # P2-2：季报场景跳过 overseas_ratio 硬过滤（数据可能 stale 或缺失）
        if overseas_required and overseas_ratio is not None:
            if overseas_ratio < config.overseas_ratio_min:
                return ScreeningResult.rejected(
                    **common, reject_reason="overseas_ratio_too_low", metrics=metrics
                )
            if overseas_ratio >= config.overseas_ratio_max:
                return ScreeningResult.rejected(
                    **common, reject_reason="overseas_ratio_abnormal", metrics=metrics
                )
        if float(pe_ttm) > config.pe_ttm_max:
            return ScreeningResult.rejected(
                **common, reject_reason="pe_ttm_too_high", metrics=metrics
            )
        enforce_overseas_yoy = overseas_required and (
            config.require_overseas_yoy
            or (
                config.enforce_overseas_yoy_when_available
                and overseas_year_count >= 2
            )
        )
        if enforce_overseas_yoy:
            if overseas_yoy is None:
                return ScreeningResult.watch(
                    **common, watch_reason="data_warning", metrics=metrics
                )
            if overseas_yoy < config.overseas_yoy_min:
                return ScreeningResult.rejected(
                    **common, reject_reason="overseas_yoy_abnormal", metrics=metrics
                )
        if config.require_cashflow_quality and not cashflow.get("cashflow_quality_passed", False):
            return ScreeningResult.rejected(
                **common, reject_reason="cashflow_quality_failed", metrics=metrics
            )
        if config.require_leverage and not leverage.get("leverage_passed", False):
            return ScreeningResult.rejected(
                **common, reject_reason="debt_ratio_too_high", metrics=metrics
            )
        # 一致预期是软条件：开启但未通过 → watch（研报覆盖不足）
        if config.require_consensus_growth and not consensus.get("consensus_passed", False):
            metrics.source_status.consensus = "missing"
            return ScreeningResult.watch(
                **common, watch_reason="consensus_missing", metrics=metrics
            )

        # P2-2：统一用 all_thresholds_met；季报场景下 source_status.overseas_parser
        # 和 metrics.overseas.overseas_ratio 已经足够说明上下文（ratio 可能为 None）。
        if config.require_consensus_growth:
            metrics.source_status.consensus = "ok"
        return ScreeningResult.hit(
            **common, hit_reason="all_thresholds_met", metrics=metrics
        )

    except Exception as e:
        return ScreeningResult.from_exception(
            **common, error=f"{type(e).__name__}: {e}", metrics=metrics
        )


# === 向后兼容旧接口 ===
def _result_to_legacy_dict(result: ScreeningResult) -> Optional[dict]:
    """把 hit 的 ScreeningResult 转回旧 CSV 行格式（保持 run_overseas_champion 兼容）。

    非 hit 状态返回 None（旧接口只输出命中）。
    缺失字段（revenue_yi / overseas_data_year 等）保持 None：旧脚本用
    `[c for c in cols if c in result.columns]` 兼容缺失列。
    """
    if result.status != Status.HIT:
        return None
    m = result.metrics
    return {
        "code": result.code,
        "name": result.name,
        "sw_first": "",
        "report_date": None,
        "overseas_revenue_yi": m.overseas.overseas_revenue_yi,
        "revenue_yi": m.overseas.total_revenue_yi,
        "overseas_ratio": m.overseas.overseas_ratio,
        "overseas_yoy": m.overseas.overseas_yoy,
        "overseas_data_year": None,
        "pe_ttm_current": m.valuation.pe_ttm,
        "pe_percentile": m.valuation.pe_pct_5y,
        # 扩展条件字段（从 metrics 拿；旧脚本按列名存在性兼容）
        "ocf_to_profit": m.quality.ocf_to_net_profit,
        "debt_ratio": m.quality.debt_ratio,
        # P1.5-6：legacy CSV 字段补全（绝对值 + 一致预期）
        "ocf_net_yi": m.quality.ocf_net_yi,
        "net_profit_yi": m.quality.net_profit_yi,
        "total_liabilities_yi": m.quality.total_liabilities_yi,
        "total_assets_yi": m.quality.total_assets_yi,
        "eps_current": m.catalyst.eps_current,
        "eps_forecast_y1": m.catalyst.eps_forecast_y1,
        "eps_forecast_y2": m.catalyst.eps_forecast_y2,
        "eps_y1_growth": m.catalyst.eps_y1_growth,
        "eps_y2_growth": m.catalyst.eps_y2_growth,
    }


def run_overseas_champion(
    source: DataSource,
    store: DuckDBStore,
    candidates: pd.DataFrame,
    config: StrategyConfig | None = None,
    show_progress: bool = True,
) -> pd.DataFrame:
    """跑策略三筛选（旧 CSV 入口，仅返回 hit 清单，向后兼容）。

    内部调用 evaluate_overseas_full 走状态化路径，保证与
    run_after_disclosure.py --strategy overseas 行为一致。
    """
    config = config or StrategyConfig()
    results = evaluate_overseas_full(
        source=source, store=store, candidates=candidates,
        run_id="legacy", period="", config=config,
        show_progress=show_progress,
    )
    rows: list[dict] = []
    for r in results:
        d = _result_to_legacy_dict(r)
        if d is not None:
            rows.append(d)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.sort_values("overseas_ratio", ascending=False).reset_index(drop=True)


def _get_sina_annual_item(
    sina_source: Optional[SinaFinancialSource],
    code: str,
    statement_type: str,
    item_en: str,
    latest_year: int,
) -> Optional[float]:
    """从新浪 financials_full 拿指定年报（latest_year-12-31）的指定科目值。"""
    if sina_source is None:
        return None
    try:
        if statement_type == "lrb":
            df = sina_source.get_income_statement(code, num=4)
        elif statement_type == "fzb":
            df = sina_source.get_balance_sheet(code, num=4)
        elif statement_type == "llb":
            df = sina_source.get_cashflow(code, num=4)
        else:
            return None
    except Exception:
        return None

    if df.empty:
        return None

    target_date = pd.Timestamp(year=latest_year, month=12, day=31)
    df = df.copy()
    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
    annual = df[df["report_date"] == target_date]
    if annual.empty:
        # 退路：最新一期年报
        annual = df[df["report_date"].dt.month == 12]
    if annual.empty:
        return None

    matched = annual[annual["item_en"] == item_en]
    if matched.empty:
        return None
    val = matched.iloc[-1].get("value")
    if pd.isna(val):
        return None
    return float(val)


def _check_consensus_growth(
    sina_source: Optional[SinaFinancialSource],
    store: DuckDBStore,
    code: str,
    latest_year: int,
    config: StrategyConfig,
) -> dict:
    """一致预期增速过滤：东财研报 EPS Y1/Y2 增速 ≥ threshold。

    当期实际 EPS 从新浪利润表 latest_year 年报的 eps_basic 取。
    Y1 增速 = 当年研报预测 EPS 均值 / 当期实际 EPS - 1
    Y2 增速 = 明年预测 EPS / 当年预测 EPS - 1
    """
    result = {
        "eps_current": None,
        "eps_forecast_y1": None,
        "eps_forecast_y2": None,
        "eps_y1_growth": None,
        "eps_y2_growth": None,
        "consensus_passed": False,
    }

    # 当期实际 EPS（新浪利润表）
    current_eps = _get_sina_annual_item(sina_source, code, "lrb", "eps_basic", latest_year)
    if current_eps is None or current_eps <= 0:
        return result
    result["eps_current"] = current_eps

    # 从 broker_reports 拿近 N 天研报
    df = store.load_broker_reports(code=code)
    if df.empty:
        return result

    df = df.copy()
    df["publish_date"] = pd.to_datetime(df["publish_date"], errors="coerce")
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=config.consensus_recent_days)
    recent = df[df["publish_date"] >= cutoff]
    if recent.empty:
        return result

    y1 = pd.to_numeric(recent["eps_forecast_y1"], errors="coerce").dropna()
    y2 = pd.to_numeric(recent["eps_forecast_y2"], errors="coerce").dropna()
    if y1.empty:
        return result

    y1_mean = float(y1.mean())
    y2_mean = float(y2.mean()) if not y2.empty else None
    result["eps_forecast_y1"] = y1_mean
    result["eps_forecast_y2"] = y2_mean

    y1_growth = y1_mean / current_eps - 1
    y2_growth = (y2_mean / y1_mean - 1) if (y2_mean and y1_mean > 0) else None
    result["eps_y1_growth"] = y1_growth
    result["eps_y2_growth"] = y2_growth

    passed = (
        y1_growth is not None and y1_growth >= config.consensus_growth_min
        and y2_growth is not None and y2_growth >= config.consensus_growth_min
    )
    result["consensus_passed"] = passed
    return result


def _check_cashflow_quality(
    sina_source: Optional[SinaFinancialSource],
    store: DuckDBStore,
    code: str,
    latest_year: int,
    latest_fin: pd.Series,
) -> dict:
    """现金流质量过滤：经营性现金流净额 / 净利润 ≥ 0.7。

    ocf_net 来自新浪现金流表；net_profit 来自 AkShare financial_abstract（latest_fin 已传入）。
    """
    result = {"ocf_net_yi": None, "net_profit_yi": None, "ocf_to_profit": None, "cashflow_quality_passed": False}

    ocf_net = _get_sina_annual_item(sina_source, code, "llb", "ocf_net", latest_year)
    if ocf_net is None:
        return result
    result["ocf_net_yi"] = ocf_net / 1e8

    net_profit = latest_fin.get("net_profit")
    if pd.isna(net_profit) or net_profit <= 0:
        return result
    result["net_profit_yi"] = float(net_profit) / 1e8

    ratio = ocf_net / net_profit
    result["ocf_to_profit"] = ratio
    result["cashflow_quality_passed"] = ratio >= 0.70
    return result


def _check_leverage(
    sina_source: Optional[SinaFinancialSource],
    code: str,
    latest_year: int,
) -> dict:
    """资产负债率过滤：latest_year 总负债/总资产 < 60%。"""
    result = {"total_liabilities_yi": None, "total_assets_yi": None, "debt_ratio": None, "leverage_passed": False}

    total_liab = _get_sina_annual_item(sina_source, code, "fzb", "total_liabilities", latest_year)
    total_assets = _get_sina_annual_item(sina_source, code, "fzb", "total_assets", latest_year)
    if total_liab is None or total_assets is None or total_assets <= 0:
        return result

    result["total_liabilities_yi"] = total_liab / 1e8
    result["total_assets_yi"] = total_assets / 1e8
    ratio = total_liab / total_assets
    result["debt_ratio"] = ratio
    result["leverage_passed"] = ratio < 0.60
    return result
