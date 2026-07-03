"""策略二：医药二A/二B的行业池、分类规则与二A MVP。

- vbp_recovery：集采冲击后修复型，适合看量价齐升和现金流修复。
- innovation_export：创新药/创新器械出海型，适合看海外催化和港股对照。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from ..screening import MetricsSchema, ScreeningResult
from ..screening.period import period_report_date


PharmaSubStrategy = Literal["vbp_recovery", "innovation_export"]


PHARMA_SW_FIRST = ("医药生物",)

VBP_RECOVERY_KEYWORDS = (
    "化学制剂",
    "化学原料药",
    "中药",
    "中成药",
    "医疗器械",
    "医疗耗材",
    "医用耗材",
    "体外诊断",
    "IVD",
    "诊断试剂",
    "骨科",
    "心血管耗材",
)

INNOVATION_EXPORT_KEYWORDS = (
    "创新药",
    "创新器械",
    "生物制品",
    "生物科技",
    "18A",
    "License-out",
    "license-out",
    "海外临床",
    "FDA",
    "国际多中心",
)

# improvements.md 中明确暂缓，不混入策略二A/二B。
PHARMA_EXCLUDED_KEYWORDS = (
    "CXO",
    "CRO",
    "CDMO",
    "医药商业",
    "医疗服务",
    "医美",
)


PHARMA_GROUND_TRUTH_COLUMNS = [
    "code",
    "name",
    "sub_strategy",
    "sub_industry",
    "vbp_batch",
    "vbp_status",
    "shock_start_quarter",
    "recovery_start_quarter",
    "recovery_quarter_count",
    "recovery_basis",
    "price_performance_window",
    "relative_return",
    "human_label",
    "label_reason",
    "label_version",
]


PHARMA_VBP_STRATEGY = "pharma_vbp"


@dataclass(frozen=True)
class PharmaIndustryClassification:
    sub_strategy: PharmaSubStrategy
    matched_keyword: str
    source_text: str


@dataclass(frozen=True)
class VbpRecoveryConfig:
    """策略二A MVP 阈值。

    financials 表里的 yoy 原始口径是百分数（10.0 = 10%），策略内部统一转小数。
    """

    min_revenue_yoy: float = 0.0
    min_net_profit_yoy: float = 0.0
    min_gross_margin_change: float = -0.005  # -0.5pp 以内视为未恶化
    min_ocf_per_share: float = 0.0
    min_checks_for_hit: int = 3
    min_checks_for_watch: int = 2
    hit_vbp_statuses: tuple[str, ...] = ("won",)


def _join_industry_text(*parts: Optional[str]) -> str:
    return " ".join(str(p).strip() for p in parts if p is not None and str(p).strip())


def classify_pharma_sub_strategy(
    *,
    sw_first: Optional[str] = None,
    sw_second: Optional[str] = None,
    sina_industry: Optional[str] = None,
    business_tags: Optional[str] = None,
) -> Optional[PharmaIndustryClassification]:
    """按行业/业务标签把医药候选粗分到策略二A/二B。

    该函数故意保守：无法明确归类时返回 None，由 watch pool 或人工标签承接。
    """
    source_text = _join_industry_text(sw_first, sw_second, sina_industry, business_tags)
    if not source_text:
        return None

    is_pharma = any(word in source_text for word in ("医药", "医疗", "生物", "制药"))
    if sw_first and sw_first not in PHARMA_SW_FIRST:
        return None
    if not sw_first and not is_pharma:
        return None
    if any(word in source_text for word in PHARMA_EXCLUDED_KEYWORDS):
        return None

    for keyword in INNOVATION_EXPORT_KEYWORDS:
        if keyword in source_text:
            return PharmaIndustryClassification(
                sub_strategy="innovation_export",
                matched_keyword=keyword,
                source_text=source_text,
            )

    for keyword in VBP_RECOVERY_KEYWORDS:
        if keyword in source_text:
            return PharmaIndustryClassification(
                sub_strategy="vbp_recovery",
                matched_keyword=keyword,
                source_text=source_text,
            )

    return None


def _to_float(value) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_percent(value) -> float | None:
    """financials 表百分数口径转小数；缺失返回 None。"""
    v = _to_float(value)
    return None if v is None else v / 100.0


def _pick_financial_row(financials: pd.DataFrame, report_date: pd.Timestamp):
    if financials.empty or "report_date" not in financials.columns:
        return None
    df = financials.copy()
    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
    rows = df[df["report_date"] == report_date]
    if rows.empty:
        return None
    return rows.iloc[-1]


def _pick_prev_year_row(financials: pd.DataFrame, report_date: pd.Timestamp):
    prev = pd.Timestamp(
        year=report_date.year - 1, month=report_date.month, day=report_date.day,
    )
    return _pick_financial_row(financials, prev)


def _pick_vbp_event(events: pd.DataFrame, report_date: pd.Timestamp):
    if events.empty:
        return None
    df = events.copy()
    if "tender_date" in df.columns:
        df["tender_date"] = pd.to_datetime(df["tender_date"], errors="coerce")
        eligible = df[df["tender_date"].isna() | (df["tender_date"] <= report_date)]
        if not eligible.empty:
            return eligible.sort_values("tender_date", na_position="first").iloc[-1]
    return df.iloc[0]


def _price_cut_pct(event) -> float | None:
    before = _to_float(event.get("price_before"))
    after = _to_float(event.get("price_after"))
    if before is None or after is None or before <= 0:
        return None
    return after / before - 1.0


def _recovery_checks(fin_row, prev_row, config: VbpRecoveryConfig) -> dict[str, dict]:
    revenue_yoy = _normalize_percent(fin_row.get("revenue_yoy"))
    net_profit_yoy = _normalize_percent(fin_row.get("net_profit_yoy"))
    gross_margin = _normalize_percent(fin_row.get("gross_margin"))
    prev_gross_margin = (
        _normalize_percent(prev_row.get("gross_margin")) if prev_row is not None else None
    )
    gross_margin_change = (
        gross_margin - prev_gross_margin
        if gross_margin is not None and prev_gross_margin is not None else None
    )
    ocf_per_share = _to_float(fin_row.get("ocf_per_share"))
    deducted_net_profit = _to_float(fin_row.get("deducted_net_profit"))

    return {
        "revenue_yoy_recovered": {
            "value": revenue_yoy,
            "threshold": config.min_revenue_yoy,
            "passed": (
                None if revenue_yoy is None
                else revenue_yoy >= config.min_revenue_yoy
            ),
            "required_for_hit": True,
        },
        "net_profit_yoy_recovered": {
            "value": net_profit_yoy,
            "threshold": config.min_net_profit_yoy,
            "passed": (
                None if net_profit_yoy is None
                else net_profit_yoy >= config.min_net_profit_yoy
            ),
            "required_for_hit": True,
        },
        "gross_margin_not_worse": {
            "value": gross_margin_change,
            "threshold": config.min_gross_margin_change,
            "passed": (
                None if gross_margin_change is None
                else gross_margin_change >= config.min_gross_margin_change
            ),
            "required_for_hit": False,
        },
        "ocf_per_share_positive": {
            "value": ocf_per_share,
            "threshold": config.min_ocf_per_share,
            "passed": (
                None if ocf_per_share is None
                else ocf_per_share >= config.min_ocf_per_share
            ),
            "required_for_hit": False,
        },
        "deducted_net_profit_positive": {
            "value": deducted_net_profit,
            "threshold": 0.0,
            "passed": (
                None if deducted_net_profit is None else deducted_net_profit > 0
            ),
            "required_for_hit": False,
        },
    }


def _build_metrics(
    *,
    classification: PharmaIndustryClassification,
    event,
    checks: dict[str, dict],
    report_date: pd.Timestamp,
) -> MetricsSchema:
    metrics = MetricsSchema()
    metrics.source_status.financials = "ok"
    metrics.source_status.extra.update({
        "pharma_sub_strategy": classification.sub_strategy,
        "matched_keyword": classification.matched_keyword,
        "vbp_status": str(event.get("vbp_status", "")),
        "vbp_batch": str(event.get("vbp_batch", "")),
        "product_name": str(event.get("product_name", "")),
        "financial_report_date": str(report_date.date()),
        "recovery_checks_json": json.dumps(checks, ensure_ascii=False, default=str),
    })
    cut = _price_cut_pct(event)
    if cut is not None:
        metrics.source_status.extra["vbp_price_cut_pct"] = f"{cut:.6f}"

    revenue_yoy = checks["revenue_yoy_recovered"]["value"]
    net_profit_yoy = checks["net_profit_yoy_recovered"]["value"]
    gross_margin_change = checks["gross_margin_not_worse"]["value"]
    ocf_per_share = checks["ocf_per_share_positive"]["value"]
    deducted_net_profit = checks["deducted_net_profit_positive"]["value"]
    metrics.growth.revenue_yoy = revenue_yoy
    metrics.growth.net_profit_yoy = net_profit_yoy
    if gross_margin_change is not None:
        metrics.source_status.extra["gross_margin_yoy_change"] = f"{gross_margin_change:.6f}"
    if ocf_per_share is not None:
        metrics.source_status.extra["ocf_per_share"] = f"{ocf_per_share:.6f}"
    if deducted_net_profit is not None:
        metrics.source_status.extra["deducted_net_profit"] = f"{deducted_net_profit:.6f}"
    return metrics


def evaluate_vbp_recovery_one(
    *,
    candidate: dict,
    financials: pd.DataFrame,
    vbp_events: pd.DataFrame,
    run_id: str,
    period: str,
    config: VbpRecoveryConfig | None = None,
) -> ScreeningResult:
    """策略二A：单股集采修复 MVP。

    判定原则：
    - 先有医药二A行业/业务归类；
    - 必须有可追溯集采结构化事件；
    - 再看下一/当前报告期财务是否呈现收入、利润、毛利率、现金流修复。
    """
    config = config or VbpRecoveryConfig()
    code = str(candidate.get("code", "")).zfill(6)
    name = candidate.get("name")
    common = {
        "run_id": run_id,
        "code": code,
        "name": name,
        "strategy": PHARMA_VBP_STRATEGY,
        "period": period,
    }
    classification = classify_pharma_sub_strategy(
        sw_first=candidate.get("sw_first"),
        sw_second=candidate.get("sw_second"),
        sina_industry=candidate.get("sina_industry"),
        business_tags=candidate.get("business_tags") or candidate.get("em2016"),
    )
    if classification is None or classification.sub_strategy != "vbp_recovery":
        return ScreeningResult.rejected(
            **common, reject_reason="not_vbp_recovery_pool",
        )

    report_date = period_report_date(period)
    if report_date is None:
        return ScreeningResult.data_missing(
            **common, data_missing_reason="invalid_period",
        )

    event = _pick_vbp_event(vbp_events, report_date)
    if event is None:
        return ScreeningResult.data_missing(
            **common, data_missing_reason="vbp_event_missing",
        )
    vbp_status = str(event.get("vbp_status", "")).strip().lower()
    if vbp_status == "not_applicable":
        return ScreeningResult.rejected(
            **common, reject_reason="not_vbp_exposed",
        )

    fin_row = _pick_financial_row(financials, report_date)
    if fin_row is None:
        return ScreeningResult.data_missing(
            **common, data_missing_reason="financial_data_missing",
        )
    prev_row = _pick_prev_year_row(financials, report_date)
    checks = _recovery_checks(fin_row, prev_row, config)
    metrics = _build_metrics(
        classification=classification,
        event=event,
        checks=checks,
        report_date=report_date,
    )
    evaluated = [c for c in checks.values() if c["passed"] is not None]
    if not evaluated:
        return ScreeningResult.data_missing(
            **common,
            data_missing_reason="financial_recovery_metrics_missing",
            metrics=metrics,
        )
    if vbp_status == "unknown" or not vbp_status:
        return ScreeningResult.watch(
            **common, watch_reason="vbp_status_unknown", metrics=metrics,
        )
    passed_count = sum(1 for c in evaluated if c["passed"] is True)
    core_revenue = checks["revenue_yoy_recovered"]["passed"] is True
    core_profit = checks["net_profit_yoy_recovered"]["passed"] is True
    hit_status_ok = vbp_status in config.hit_vbp_statuses

    if (
        core_revenue and core_profit
        and passed_count >= config.min_checks_for_hit
        and hit_status_ok
    ):
        return ScreeningResult.hit(
            **common, hit_reason="vbp_recovery_confirmed", metrics=metrics,
        )
    if core_revenue and core_profit and passed_count >= config.min_checks_for_hit:
        return ScreeningResult.watch(
            **common,
            watch_reason=f"financial_recovery_but_vbp_status_{vbp_status}",
            metrics=metrics,
        )
    if (core_revenue or core_profit) and passed_count >= config.min_checks_for_watch:
        return ScreeningResult.watch(
            **common, watch_reason="partial_vbp_recovery", metrics=metrics,
        )
    return ScreeningResult.rejected(
        **common, reject_reason="vbp_recovery_not_confirmed", metrics=metrics,
    )


def evaluate_vbp_recovery_batch(
    *,
    candidates: pd.DataFrame,
    financials_by_code: dict[str, pd.DataFrame],
    vbp_events: pd.DataFrame,
    run_id: str,
    period: str,
    config: VbpRecoveryConfig | None = None,
) -> list[ScreeningResult]:
    """批量评估策略二A。"""
    if candidates.empty:
        return []
    config = config or VbpRecoveryConfig()
    events = vbp_events.copy() if not vbp_events.empty else pd.DataFrame()
    if "code" in events.columns:
        events["code"] = events["code"].astype(str).str.zfill(6)
    out: list[ScreeningResult] = []
    for _, row in candidates.iterrows():
        code = str(row.get("code", "")).zfill(6)
        code_events = (
            events[events["code"] == code] if "code" in events.columns else pd.DataFrame()
        )
        try:
            out.append(
                evaluate_vbp_recovery_one(
                    candidate=row.to_dict(),
                    financials=financials_by_code.get(code, pd.DataFrame()),
                    vbp_events=code_events,
                    run_id=run_id,
                    period=period,
                    config=config,
                )
            )
        except Exception as e:
            out.append(
                ScreeningResult.from_exception(
                    run_id=run_id,
                    code=code,
                    name=row.get("name"),
                    strategy=PHARMA_VBP_STRATEGY,
                    period=period,
                    error=f"{type(e).__name__}: {e}",
                )
            )
    return out
