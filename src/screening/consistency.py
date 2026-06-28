"""P2-4 财报 vs 研报一致性校验。

三个维度的软校验（参见 IMPROVEMENTS P2-4）：
1. 海外收入（财报 overseas_revenue）vs 研报海外业务描述（broker_reports.title / 命中关键词）
2. 研报 EPS 预测（broker_reports.eps_forecast_y1）vs 财报实际 EPS（financials_full）
3. 研报订单/产能/区域描述 vs 财报分地区收入（lightweight：研报标题是否提到对应区域）

输出均为软证据（observations / warnings），不直接作为硬过滤；
策略层可把 warnings 写入 metrics.catalyst.consistency_warnings 或附加到 Markdown 报告。

数据依赖：
- overseas_revenue（已落库）
- broker_reports（已落库，含 publish_date / eps_forecast_y1/y2）
- financials_full（新浪利润表，item_en='eps_basic'）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from ..storage import DuckDBStore


# 一致性观察的严重度
SEVERITY_INFO = "info"
SEVERITY_WARN = "warn"
SEVERITY_ERROR = "error"

# 研报 EPS 预测 vs 财报实际 EPS 的容忍偏差（25%）
EPS_TOLERANCE = 0.25

# 海外收入单位 → 元（与 strategies/overseas_champion.py 对齐）
_REVENUE_UNIT_TO_YUAN = {
    "元": 1.0, "千元": 1_000.0, "万元": 10_000.0,
    "百万": 1_000_000.0, "亿元": 100_000_000.0,
}

# 关键词（用于研报标题/正文搜索）
OVERSEAS_KEYWORDS = (
    "海外", "境外", "出口", "国际", "海外市场", "全球化",
)
REGION_KEYWORDS = (
    "欧洲", "美洲", "北美", "东南亚", "亚洲", "非洲", "中东", "澳洲", "一带一路",
)
ORDER_KEYWORDS = (
    "订单", "中标", "签约", "合同", "产能",
)


@dataclass
class ConsistencyObservation:
    """单条一致性观察。"""
    code: str
    kind: str  # eps_deviation / overseas_missing_in_reports / region_match / order_signal
    severity: str  # info / warn / error
    message: str
    detail: Optional[dict] = None  # 可选的结构化字段（数值/偏差等）


@dataclass
class ConsistencyResult:
    """单只股票的一致性校验结果。"""
    code: str
    observations: list[ConsistencyObservation] = field(default_factory=list)

    @property
    def has_warning(self) -> bool:
        return any(o.severity == SEVERITY_WARN for o in self.observations)

    @property
    def has_error(self) -> bool:
        return any(o.severity == SEVERITY_ERROR for o in self.observations)

    @property
    def warning_count(self) -> int:
        return sum(1 for o in self.observations if o.severity == SEVERITY_WARN)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "observations": [
                {"kind": o.kind, "severity": o.severity, "message": o.message}
                for o in self.observations
            ],
        }


def _contains_any(text: str, keywords: tuple[str, ...]) -> Optional[str]:
    """若 text 含任意 keyword，返回命中的第一个；否则 None。"""
    if not text:
        return None
    for kw in keywords:
        if kw in text:
            return kw
    return None


def check_eps_consistency(
    store: DuckDBStore,
    code: str,
    report_year: int,
    *,
    eps_df: Optional[pd.DataFrame] = None,
    reports_df: Optional[pd.DataFrame] = None,
    tolerance: float = EPS_TOLERANCE,
) -> list[ConsistencyObservation]:
    """研报 EPS Y1 预测 vs 财报实际 EPS。

    当研报 Y1 预测对应 report_year（年报）时，直接对比。
    偏差超过 tolerance → warn。
    缺失任一数据 → 不产生 observation。

    可选预加载参数（用于批量场景避免 N+1）：
    - eps_df: 已 load 的 financials_full（含 code 列）
    - reports_df: 已 load 的 broker_reports（含 code 列）
    """
    obs: list[ConsistencyObservation] = []

    # 财报实际 EPS（新浪 financials_full）
    if eps_df is None:
        try:
            eps_df = store.load_financials_full(
                code=code, statement_type="lrb", item_en="eps_basic"
            )
        except Exception:
            return obs
    else:
        eps_df = eps_df[eps_df["code"].astype(str).str.zfill(6) == str(code).zfill(6)]
    if eps_df.empty:
        return obs
    eps_df = eps_df.copy()
    eps_df["report_date"] = pd.to_datetime(eps_df["report_date"], errors="coerce")
    target = pd.Timestamp(year=report_year, month=12, day=31)
    matched = eps_df[eps_df["report_date"] == target]
    if matched.empty:
        matched = eps_df[eps_df["report_date"].dt.month == 12]
    if matched.empty:
        return obs
    actual_eps = matched.iloc[-1].get("value")
    if pd.isna(actual_eps) or actual_eps is None or float(actual_eps) <= 0:
        return obs
    actual_eps = float(actual_eps)

    # 研报 EPS Y1 预测（broker_reports，近 90 天）
    if reports_df is None:
        try:
            reports = store.load_broker_reports(code=code)
        except Exception:
            return obs
    else:
        reports = reports_df[
            reports_df["code"].astype(str).str.zfill(6) == str(code).zfill(6)
        ]
    if reports.empty:
        return obs
    reports = reports.copy()
    reports["publish_date"] = pd.to_datetime(reports["publish_date"], errors="coerce")
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=90)
    recent = reports[reports["publish_date"] >= cutoff]
    if recent.empty:
        return obs

    y1 = pd.to_numeric(recent["eps_forecast_y1"], errors="coerce").dropna()
    if y1.empty:
        return obs
    forecast_eps = float(y1.mean())

    deviation = abs(forecast_eps - actual_eps) / actual_eps
    detail = {
        "actual_eps": actual_eps,
        "forecast_eps_mean": forecast_eps,
        "deviation_pct": round(deviation * 100, 1),
    }
    if deviation > tolerance:
        obs.append(ConsistencyObservation(
            code=code, kind="eps_deviation", severity=SEVERITY_WARN,
            message=(
                f"研报 EPS Y1 预测 {forecast_eps:.2f} 与财报实际 {actual_eps:.2f} "
                f"偏差 {deviation*100:.1f}% > 容忍 {tolerance*100:.0f}%"
            ),
            detail=detail,
        ))
    else:
        obs.append(ConsistencyObservation(
            code=code, kind="eps_deviation", severity=SEVERITY_INFO,
            message=(
                f"研报 EPS Y1 预测 {forecast_eps:.2f} 与财报实际 {actual_eps:.2f} "
                f"偏差 {deviation*100:.1f}%（在容忍内）"
            ),
            detail=detail,
        ))
    return obs


def check_overseas_consistency(
    store: DuckDBStore,
    code: str,
    report_year: int,
    *,
    overseas_df: Optional[pd.DataFrame] = None,
    reports_df: Optional[pd.DataFrame] = None,
) -> list[ConsistencyObservation]:
    """财报海外收入 vs 研报海外业务描述。

    逻辑：
    - 若财报披露了较高海外收入（>30%），但近期研报标题/关键词中
      没有任何"海外/境外/出口"等字样 → warn（研报可能漏覆盖）
    - 若有匹配 → info（带区域 / 订单信号）

    可选预加载参数（用于批量场景避免 N+1）。
    """
    obs: list[ConsistencyObservation] = []

    # 财报海外收入
    if overseas_df is None:
        try:
            overseas = store.load_overseas_revenue()
        except Exception:
            return obs
    else:
        overseas = overseas_df
    if overseas.empty:
        return obs
    overseas = overseas.copy()
    overseas["stock_code"] = overseas["stock_code"].astype(str).str.zfill(6)
    code_str = str(code).zfill(6)
    row = overseas[
        (overseas["stock_code"] == code_str)
        & (overseas["report_year"] == report_year)
    ]
    if row.empty:
        return obs
    overseas_amt = (
        float(row.iloc[-1]["revenue"])
        * _REVENUE_UNIT_TO_YUAN.get(row.iloc[-1]["revenue_unit"], 1.0)
    )
    if overseas_amt <= 0:
        return obs

    # 研报文本（title 字段）
    if reports_df is None:
        try:
            reports = store.load_broker_reports(code=code)
        except Exception:
            return obs
    else:
        reports = reports_df[
            reports_df["code"].astype(str).str.zfill(6) == str(code).zfill(6)
        ]
    if reports.empty:
        # 财报有海外收入但无研报 → warn（被忽视证据）
        obs.append(ConsistencyObservation(
            code=code, kind="overseas_missing_in_reports",
            severity=SEVERITY_WARN,
            message=(
                f"财报 {report_year} 海外收入 {(overseas_amt/1e8):.2f} 亿，"
                "但近期无任何研报覆盖（被忽视信号）"
            ),
        ))
        return obs

    reports = reports.copy()
    reports["publish_date"] = pd.to_datetime(reports["publish_date"], errors="coerce")
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=180)
    recent = reports[reports["publish_date"] >= cutoff]
    if recent.empty:
        recent = reports
    titles = recent["title"].dropna().astype(str).tolist()
    joined = " ".join(titles)

    # 海外关键词命中
    hit_kw = _contains_any(joined, OVERSEAS_KEYWORDS)
    if hit_kw is None:
        obs.append(ConsistencyObservation(
            code=code, kind="overseas_missing_in_reports",
            severity=SEVERITY_WARN,
            message=(
                f"财报 {report_year} 海外收入 {(overseas_amt/1e8):.2f} 亿，"
                f"但近 {len(recent)} 篇研报标题未提海外/境外/出口"
            ),
        ))
    else:
        region_kw = _contains_any(joined, REGION_KEYWORDS)
        order_kw = _contains_any(joined, ORDER_KEYWORDS)
        extras = []
        if region_kw:
            extras.append(f"区域 {region_kw}")
        if order_kw:
            extras.append(f"订单/产能 {order_kw}")
        msg = (
            f"研报与海外收入一致：命中关键词 {hit_kw}"
            + (f"，含 {', '.join(extras)}" if extras else "")
        )
        obs.append(ConsistencyObservation(
            code=code, kind="region_match" if region_kw else "overseas_match",
            severity=SEVERITY_INFO, message=msg,
        ))
    return obs


def check_consistency(
    store: DuckDBStore,
    code: str,
    report_year: int,
) -> ConsistencyResult:
    """单只股票的完整一致性校验（EPS + 海外）。"""
    result = ConsistencyResult(code=code)
    result.observations.extend(
        check_eps_consistency(store, code, report_year)
    )
    result.observations.extend(
        check_overseas_consistency(store, code, report_year)
    )
    return result


def check_consistency_batch(
    store: DuckDBStore,
    codes: list[str],
    report_year: int,
) -> dict[str, ConsistencyResult]:
    """批量校验。返回 {code: ConsistencyResult}。

    一次性预加载 financials_full / broker_reports / overseas_revenue，
    避免单只校验时的 N+1 查询。
    """
    # 预加载（best-effort，失败则单只回退）
    try:
        eps_df = store.load_financials_full(statement_type="lrb", item_en="eps_basic")
    except Exception:
        eps_df = None
    try:
        reports_df = store.load_broker_reports()
    except Exception:
        reports_df = None
    try:
        overseas_df = store.load_overseas_revenue()
    except Exception:
        overseas_df = None

    out: dict[str, ConsistencyResult] = {}
    for code in codes:
        code_str = str(code).zfill(6)
        try:
            r = ConsistencyResult(code=code_str)
            r.observations.extend(check_eps_consistency(
                store, code_str, report_year,
                eps_df=eps_df, reports_df=reports_df,
            ))
            r.observations.extend(check_overseas_consistency(
                store, code_str, report_year,
                overseas_df=overseas_df, reports_df=reports_df,
            ))
            out[code_str] = r
        except Exception as e:
            r = ConsistencyResult(code=code_str)
            r.observations.append(ConsistencyObservation(
                code=code_str, kind="check_error", severity=SEVERITY_ERROR,
                message=f"一致性校验异常: {type(e).__name__}: {e}",
            ))
            out[code_str] = r
    return out
