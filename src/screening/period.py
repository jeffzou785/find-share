"""P2-2：报告期解析工具。

把 period 字符串（如 "2025A" / "2025H" / "2025Q1" / "2025Q3"）解析为
结构化信息，供策略层决定哪些过滤适用。

约定（参见 IMPROVEMENTS P2-2）：
- 年报 (A)：完整附注，海外收入数据可靠
- 半年报 (H)：附注较全，海外收入可作为参考
- 一季报 (Q1) / 三季报 (Q3)：通常没有完整分地区收入附注，
  策略三不强求海外收入更新，转为收入/利润/现金流/订单线索观察
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# 报告类型常量
KIND_ANNUAL = "annual"
KIND_HALF_YEAR = "half_year"
KIND_Q1 = "q1"
KIND_Q3 = "q3"

# period 后缀 → report_type 映射
SUFFIX_TO_KIND: dict[str, str] = {
    "A": KIND_ANNUAL,
    "H": KIND_HALF_YEAR,
    "Q1": KIND_Q1,
    "Q3": KIND_Q3,
}

# 反向映射（report_type → period suffix），用于生成 period
KIND_TO_SUFFIX: dict[str, str] = {v: k for k, v in SUFFIX_TO_KIND.items()}


@dataclass(frozen=True)
class PeriodInfo:
    """period 解析结果。

    Attributes:
        raw: 原始 period 字符串（如 "2025A"）
        year: 报告期年份（如 2025）
        suffix: 后缀（"A" / "H" / "Q1" / "Q3"）
        kind: 报告类型，"annual" / "half_year" / "q1" / "q3"
        is_annual: 是否年报
        has_overseas_notes: 该类报告是否有完整分地区收入附注
            年报 / 半年报为 True，季报为 False（季报一般只有几个关键科目）
    """
    raw: str
    year: int
    suffix: str
    kind: str
    is_annual: bool
    has_overseas_notes: bool


def parse_period(period: str) -> Optional[PeriodInfo]:
    """解析 period 字符串。

    支持格式：
    - "2025A" / "2025a" → 年报
    - "2025H" / "2025h" → 半年报
    - "2025Q1" / "2025q1" → 一季报
    - "2025Q3" / "2025q3" → 三季报

    不识别的格式返回 None。
    """
    if not period or not isinstance(period, str):
        return None
    s = period.strip().upper()
    if len(s) < 5:
        return None
    year_str = s[:4]
    if not year_str.isdigit():
        return None
    year = int(year_str)
    suffix = s[4:]
    kind = SUFFIX_TO_KIND.get(suffix)
    if kind is None:
        return None
    return PeriodInfo(
        raw=period,
        year=year,
        suffix=suffix,
        kind=kind,
        is_annual=(kind == KIND_ANNUAL),
        has_overseas_notes=kind in (KIND_ANNUAL, KIND_HALF_YEAR),
    )


def require_overseas_filter(period: str) -> bool:
    """该 period 是否应该跑策略三的海外收入硬过滤。

    年报和半年报：True（附注完整，可严格过滤）
    季报：False（无完整附注，软观察）
    无法解析：True（保守起见，沿用旧行为）
    """
    info = parse_period(period)
    if info is None:
        return True
    return info.has_overseas_notes
