"""策略二：医药二A/二B的行业池和基础分类规则。

这里不直接做选股打分，只沉淀最小可测试的分类规则：
- vbp_recovery：集采冲击后修复型，适合看量价齐升和现金流修复。
- innovation_export：创新药/创新器械出海型，适合看海外催化和港股对照。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


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


@dataclass(frozen=True)
class PharmaIndustryClassification:
    sub_strategy: PharmaSubStrategy
    matched_keyword: str
    source_text: str


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
