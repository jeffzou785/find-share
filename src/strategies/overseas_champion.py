"""策略三：出海隐形冠军。

逻辑：
- 行业：EM2016/申万一级 机械设备 / 交运设备(汽车) / 基础化工
- 海外业务：最新年报境外收入占比 > 30%
- 海外增速：境外收入同比 > 40%（需 2 个年度数据；无则跳过此条件）
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

from dataclasses import dataclass
from typing import Optional

import pandas as pd
from tqdm import tqdm

from ..collectors.base import DataSource
from ..collectors.sina_impl import SinaFinancialSource
from ..indicators.valuation import compute_pe_pb_percentile
from ..screening import MetricsSchema, ScreeningResult, Status
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
    require_overseas_yoy: bool = False  # 当前只入库 1 年数据时跳过增速校验
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


# === P1-2 被忽视证据 ===
# 当 reports_count_90d <= 此阈值时，认为研报覆盖不足（被忽视证据之一）
NEGLECT_LOW_REPORT_THRESHOLD = 3
# 默认统计窗口
DEFAULT_REPORT_LOOKBACK_DAYS = 90


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
    """
    config = config or StrategyConfig()
    if candidates.empty:
        return []

    pool = candidates[candidates["sw_first"].isin(TARGET_INDUSTRIES)].copy()
    if pool.empty:
        return []

    # 加载 overseas_revenue，构造 overseas_map（含 P1-3 parse_warning 元数据）
    overseas_df = store.load_overseas_revenue()
    overseas_map: dict[str, dict[int, float]] = {}
    overseas_meta: dict[tuple[str, int], dict] = {}  # (code, year) → {parse_warning, confidence}
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
            # P1-3：保留 parse_warning / confidence 元数据
            overseas_meta[(row["stock_code"], int(row["report_year"]))] = {
                "parse_warning": row.get("parse_warning"),
                "confidence": row.get("confidence"),
            }

    if sina_source is None:
        try:
            sina_source = SinaFinancialSource()
        except Exception:
            sina_source = None

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
            run_id=run_id, period=period,
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
) -> ScreeningResult:
    """单股评估，返回带 status + reason + metrics 的 ScreeningResult。"""
    metrics = MetricsSchema()
    metrics.source_status.financials = "ok"
    metrics.source_status.valuation = "ok"
    metrics.source_status.overseas_parser = "ok"
    metrics.source_status.consensus = "skipped"
    common = dict(run_id=run_id, code=code, name=name,
                  strategy="overseas", period=period)

    try:
        # 1. overseas_revenue 数据
        if code not in overseas_map:
            metrics.source_status.overseas_parser = "missing"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="overseas_revenue_missing",
                metrics=metrics,
            )

        yearly = overseas_map[code]
        latest_year = max(yearly.keys())
        overseas_revenue = yearly[latest_year]
        if overseas_revenue <= 0:
            metrics.source_status.overseas_parser = "missing"
            return ScreeningResult.data_missing(
                **common, data_missing_reason="overseas_revenue_missing",
                metrics=metrics,
            )

        overseas_yoy: Optional[float] = None
        if latest_year - 1 in yearly:
            prev = yearly[latest_year - 1]
            if prev > 0:
                overseas_yoy = (overseas_revenue - prev) / prev

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
        target_date = pd.Timestamp(year=latest_year, month=12, day=31)
        annual_rows = fin_sorted[
            pd.to_datetime(fin_sorted["report_date"]) == target_date
        ]
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

        overseas_ratio = overseas_revenue / revenue

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
        consensus = _check_consensus_growth(sina_source, store, code, latest_year, config)
        cashflow = _check_cashflow_quality(sina_source, store, code, latest_year, latest_fin)
        leverage = _check_leverage(sina_source, code, latest_year)

        # 5. 填充 metrics
        metrics.valuation.pe_ttm = float(pe_ttm)
        metrics.valuation.pe_pct_5y = pe_stat["percentile"]
        metrics.overseas.overseas_ratio = float(overseas_ratio)
        metrics.overseas.overseas_yoy = float(overseas_yoy) if overseas_yoy is not None else None
        metrics.overseas.overseas_revenue_yi = float(overseas_revenue) / 1e8
        if cashflow.get("ocf_to_profit") is not None:
            metrics.quality.ocf_to_net_profit = float(cashflow["ocf_to_profit"])
        if leverage.get("debt_ratio") is not None:
            metrics.quality.debt_ratio = float(leverage["debt_ratio"])
        # P1-2：研报覆盖度（被忽视证据之一；不改硬过滤，仅填 metrics）
        metrics.catalyst.reports_count_90d = _compute_reports_coverage(
            store, code, DEFAULT_REPORT_LOOKBACK_DAYS
        )

        # 6. 同比异常 → watch（parse_warning），不直接 rejected
        if config.sanity_check_yoy and is_yoy_anomaly(overseas_yoy):
            metrics.overseas.parse_warning = (
                f"overseas_yoy_anomaly: {overseas_yoy:.2f}"
            )
            return ScreeningResult.watch(
                **common, watch_reason="parse_warning", metrics=metrics
            )

        # 6.5 P1-3：overseas_revenue 表里携带的 parse_warning（多年交叉校验/低置信度）
        meta = overseas_meta.get((code, latest_year), {})
        db_parse_warning = meta.get("parse_warning")
        confidence = meta.get("confidence")
        if db_parse_warning:
            metrics.overseas.parse_warning = db_parse_warning
            return ScreeningResult.watch(
                **common, watch_reason="parse_warning", metrics=metrics
            )
        if confidence == "low":
            metrics.overseas.parse_warning = (
                f"low_confidence_parser:region={yearly and max(yearly.keys())}"
            )
            return ScreeningResult.watch(
                **common, watch_reason="data_warning", metrics=metrics
            )

        # 7. 阈值检查
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
        if config.require_overseas_yoy and overseas_yoy is not None:
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
        "revenue_yi": None,
        "overseas_ratio": m.overseas.overseas_ratio,
        "overseas_yoy": m.overseas.overseas_yoy,
        "overseas_data_year": None,
        "pe_ttm_current": m.valuation.pe_ttm,
        "pe_percentile": m.valuation.pe_pct_5y,
        # 扩展条件字段（从 metrics 拿；旧脚本按列名存在性兼容）
        "ocf_to_profit": m.quality.ocf_to_net_profit,
        "debt_ratio": m.quality.debt_ratio,
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
