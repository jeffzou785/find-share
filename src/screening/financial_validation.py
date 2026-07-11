"""P2：下一期财务兑现验证。

给定某次 screen_run 的 hit/watch 候选，找到下一报告期，读取本地
financials 表，判断收入/净利润同比是否继续兑现。验证结果只用于复盘和
阈值校准，不反向修改原始筛选状态。
"""
from __future__ import annotations

import json
from typing import Iterable

import pandas as pd

from .period import next_period, period_report_date


DEFAULT_VALIDATION_STATUSES: tuple[str, ...] = ("hit", "watch")


def _to_float(value) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_yoy(value) -> float | None:
    """统一同比单位到小数。

    项目 `financials` 表沿用 AkShare 百分数口径（20.0 = 20%），验证层统一
    除以 100，避免把 0.8% 误判成 80%。
    """
    v = _to_float(value)
    if v is None:
        return None
    return v / 100.0


def _pick_financial_row(financials: pd.DataFrame, report_date: pd.Timestamp):
    if financials.empty or "report_date" not in financials.columns:
        return None
    df = financials.copy()
    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
    rows = df[df["report_date"] == report_date]
    if rows.empty:
        return None
    return rows.iloc[-1]


def _verdict_from_checks(checks: dict[str, dict]) -> str:
    required = [v for v in checks.values() if v.get("required")]
    evaluated = [v for v in required if v.get("passed") is not None]
    if not evaluated:
        return "insufficient_data"
    passed = sum(1 for v in evaluated if v.get("passed") is True)
    if passed == len(evaluated):
        return "confirmed"
    if passed == 0:
        return "deteriorated"
    return "mixed"


def validate_next_financials(
    *,
    candidate: dict,
    financials: pd.DataFrame,
    validation_period: str | None = None,
    min_revenue_yoy: float = 0.05,
    min_net_profit_yoy: float = 0.05,
) -> dict:
    """验证单个候选的下一期财务表现。"""
    code = str(candidate.get("code", "")).zfill(6)
    source_period = candidate.get("period")
    target_period = validation_period or next_period(str(source_period or ""))
    row = {
        "run_id": candidate.get("run_id"),
        "code": code,
        "name": candidate.get("name"),
        "strategy": candidate.get("strategy"),
        "candidate_status": candidate.get("status"),
        "source_period": source_period,
        "validation_period": target_period,
        "validation_report_date": None,
        "verdict": "pending",
        "revenue_yoy": None,
        "net_profit_yoy": None,
        "deducted_net_profit": None,
        "gross_margin": None,
        "ocf_per_share": None,
        "checks_json": None,
        "error": None,
    }
    if not target_period:
        row["verdict"] = "skipped"
        row["error"] = "invalid_source_period"
        return row

    report_date = period_report_date(target_period)
    if report_date is None:
        row["verdict"] = "skipped"
        row["error"] = "invalid_validation_period"
        return row
    row["validation_report_date"] = report_date

    fin_row = _pick_financial_row(financials, report_date)
    if fin_row is None:
        row["error"] = "validation_financial_missing"
        return row

    revenue_yoy = _normalize_yoy(fin_row.get("revenue_yoy"))
    net_profit_yoy = _normalize_yoy(fin_row.get("net_profit_yoy"))
    deducted_net_profit = _to_float(fin_row.get("deducted_net_profit"))
    gross_margin_raw = _to_float(fin_row.get("gross_margin"))
    gross_margin = (
        gross_margin_raw / 100.0 if gross_margin_raw is not None else None
    )
    ocf_per_share = _to_float(fin_row.get("ocf_per_share"))

    checks = {
        "revenue_yoy": {
            "value": revenue_yoy,
            "threshold": min_revenue_yoy,
            "passed": (
                None if revenue_yoy is None else revenue_yoy >= min_revenue_yoy
            ),
            "required": True,
        },
        "net_profit_yoy": {
            "value": net_profit_yoy,
            "threshold": min_net_profit_yoy,
            "passed": (
                None if net_profit_yoy is None
                else net_profit_yoy >= min_net_profit_yoy
            ),
            "required": True,
        },
        "deducted_net_profit_positive": {
            "value": deducted_net_profit,
            "threshold": 0.0,
            "passed": (
                None if deducted_net_profit is None else deducted_net_profit > 0
            ),
            "required": False,
        },
    }

    row.update({
        "verdict": _verdict_from_checks(checks),
        "revenue_yoy": revenue_yoy,
        "net_profit_yoy": net_profit_yoy,
        "deducted_net_profit": deducted_net_profit,
        "gross_margin": gross_margin,
        "ocf_per_share": ocf_per_share,
        "checks_json": json.dumps(checks, ensure_ascii=False),
    })
    return row


def validate_next_financials_batch(
    *,
    candidates: pd.DataFrame,
    financials_loader,
    validation_period: str | None = None,
    statuses: Iterable[str] = DEFAULT_VALIDATION_STATUSES,
    min_revenue_yoy: float = 0.05,
    min_net_profit_yoy: float = 0.05,
) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    allowed = set(statuses)
    filtered = (
        candidates[candidates["status"].isin(allowed)].copy()
        if "status" in candidates.columns else candidates.copy()
    )
    rows: list[dict] = []
    for _, candidate_row in filtered.iterrows():
        candidate = candidate_row.to_dict()
        code = str(candidate.get("code", "")).zfill(6)
        rows.append(
            validate_next_financials(
                candidate=candidate,
                financials=financials_loader(code),
                validation_period=validation_period,
                min_revenue_yoy=min_revenue_yoy,
                min_net_profit_yoy=min_net_profit_yoy,
            )
        )
    return pd.DataFrame(rows)


def summarize_financial_validation(results: pd.DataFrame) -> dict:
    if results.empty:
        return {"total_rows": 0, "verdicts": {}}
    verdicts = {
        str(k): int(v)
        for k, v in results["verdict"].value_counts(dropna=False).to_dict().items()
    }
    out = {
        "total_rows": int(len(results)),
        "verdicts": verdicts,
    }
    for col in ("revenue_yoy", "net_profit_yoy"):
        if col in results and results[col].notna().any():
            out[f"avg_{col}"] = round(float(results[col].mean()), 6)
        else:
            out[f"avg_{col}"] = None
    return out
