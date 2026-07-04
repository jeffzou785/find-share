"""状态枚举和原因码（P0-5 / P0-6）。

状态语义：
- hit         硬过滤通过，关键数据完整，进入正式候选
- watch       主线逻辑有吸引力，但存在软缺陷或数据疑点
- rejected    硬风控或核心阈值失败
- data_missing 必要数据缺失，当前不能判断
- error       代码异常、写库失败、解析异常

映射原则：
- 硬风控短路（ST / 退市 / 财务异常 / 解析失败）→ rejected
- 软缺陷（parse_warning / 一致预期缺失 / 阈值边界）→ watch
- 必要数据缺失（PDF 未下载 / PE 历史为空）→ data_missing
- 程序异常（解析抛异常 / 写库失败）→ error
"""
from __future__ import annotations

from enum import Enum


class Status(str, Enum):
    HIT = "hit"
    WATCH = "watch"
    REJECTED = "rejected"
    DATA_MISSING = "data_missing"
    ERROR = "error"

    def __str__(self) -> str:
        return self.value


class RunStatus(str, Enum):
    """screen_runs.status。"""

    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


# === 策略一 reject_reason ===
REJECT_CONSUMER = {
    "not_target_consumer_industry",
    "pe_history_missing",
    "pe_percentile_too_high",
    "deducted_profit_missing",
    "deducted_yoy_too_low",
    "not_inflection_or_trend",
    "market_cap_too_small",
    "st_or_delisting",
    # P1-1 新增
    "pb_percentile_too_high",
    "revenue_yoy_too_low",
    "gross_margin_deteriorating",
    # P1 策略一经营质量
    "cashflow_quality_failed",
    "deducted_profit_quality_failed",
}

# === 策略三 reject_reason ===
REJECT_OVERSEAS = {
    "not_target_manufacturing_industry",
    "overseas_revenue_missing",
    "overseas_ratio_too_low",
    "overseas_ratio_abnormal",
    "overseas_yoy_abnormal",
    "pe_ttm_too_high",
    "cashflow_quality_failed",
    "debt_ratio_too_high",
    "financial_data_missing",
}

# === watch_reason（两策略通用）===
WATCH_REASONS = {
    "parse_warning",        # 海外收入单位识别疑点、跨页断字
    "consensus_missing",    # 一致预期研报缺失
    "near_threshold",       # 阈值边界附近
    "low_report_coverage",  # 研报覆盖不足
    "weak_price_confirmation",  # 价格未确认基本面
    "data_warning",         # 估算或低可信解析
}

# === data_missing_reason ===
DATA_MISSING_REASONS = {
    "pdf_not_downloaded",
    "pe_history_empty",
    "financials_empty",
    "overseas_revenue_empty",
    "disclosures_table_empty",
    "report_not_found",
}


def validate_reason_codes() -> dict[str, set[str]]:
    """供测试导入校验。返回所有合法原因码集合。"""
    return {
        "reject_consumer": set(REJECT_CONSUMER),
        "reject_overseas": set(REJECT_OVERSEAS),
        "watch": set(WATCH_REASONS),
        "data_missing": set(DATA_MISSING_REASONS),
    }
