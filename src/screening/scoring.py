"""P2-1 评分层：把 metrics 转成 0-1 的子分 + 加权 final_score。

设计约束（参见 IMPROVEMENTS P2-1）：
- 权重写入 screen_runs.config_json（ConfigSchema.score_weights）。
- 评分只用于排序和 watch 分层，不覆盖硬风控。
- 子分缺失时按 0.5 中性填充（避免拉低 / 拉高），并在权重重新归一化时排除缺失项。

公式：
    final_score =
      growth_score * W_GROWTH
      + valuation_score * W_VALUATION
      + quality_score * W_QUALITY
      + catalyst_score * W_CATALYST
      + neglect_score * W_NEGLECT
      - risk_penalty

子分计算（各 0-1）：
- growth_score：扣非 TTM 同比、营收同比、（策略三）海外同比
- valuation_score：PE 分位、PB 分位、PE-TTM 绝对值（越低越高）
- quality_score：毛利率、OCF/净利润、资产负债率（越低越高）
- catalyst_score：一致预期 Y1/Y2 增速、研报覆盖数（在一定范围内得分高）
- neglect_score：研报覆盖少 + 新闻少 + 非 AI 概念（用于策略三"被忽视"主线）
- risk_penalty：parse_warning + 低现金流 + 高负债
"""
from __future__ import annotations

from typing import Optional

from .schemas import MetricsSchema, ScoreMetrics


# === 默认权重（按策略差异化） ===
DEFAULT_WEIGHTS_CONSUMER: dict[str, float] = {
    "growth": 0.30,
    "valuation": 0.25,
    "quality": 0.25,
    "catalyst": 0.20,
    "neglect": 0.00,
}

DEFAULT_WEIGHTS_OVERSEAS: dict[str, float] = {
    "growth": 0.25,
    "valuation": 0.20,
    "quality": 0.20,
    "catalyst": 0.15,
    "neglect": 0.20,
}

# penalty 是直接减项，不参与权重归一化
DEFAULT_RISK_PENALTY_WEIGHT: float = 0.15


def default_weights(strategy: str) -> dict[str, float]:
    """按策略返回默认权重。未识别策略回退到 overseas。"""
    if strategy == "consumer":
        return dict(DEFAULT_WEIGHTS_CONSUMER)
    return dict(DEFAULT_WEIGHTS_OVERSEAS)


def _clamp(x: Optional[float], lo: float = 0.0, hi: float = 1.0) -> Optional[float]:
    if x is None:
        return None
    if x != x:  # NaN
        return None
    return max(lo, min(hi, x))


def _norm_high_better(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    """值越大得分越高：线性映射 [lo, hi] → [0, 1]。"""
    if value is None:
        return None
    if hi <= lo:
        return None
    return (value - lo) / (hi - lo)


def _norm_low_better(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    """值越小得分越高：线性映射 [lo, hi] → [1, 0]。"""
    if value is None:
        return None
    if hi <= lo:
        return None
    return (hi - value) / (hi - lo)


def _avg(values: list[Optional[float]]) -> Optional[float]:
    """忽略 None 求平均；全 None 时返回 None。"""
    valid = [v for v in values if v is not None]
    if not valid:
        return None
    return sum(valid) / len(valid)


def compute_growth_score(metrics: MetricsSchema, strategy: str) -> Optional[float]:
    """增长子分（0-1）。"""
    parts: list[Optional[float]] = []

    deducted = metrics.growth.deducted_profit_yoy_ttm
    if deducted is not None:
        # 扣非 TTM 同比：[-50%, +200%] → [0, 1]
        parts.append(_norm_high_better(deducted, -0.5, 2.0))

    rev_yoy = metrics.growth.revenue_yoy
    if rev_yoy is not None:
        # 营收同比：[-20%, +80%] → [0, 1]
        parts.append(_norm_high_better(rev_yoy, -0.2, 0.8))

    if strategy == "overseas":
        overseas_yoy = metrics.overseas.overseas_yoy
        if overseas_yoy is not None:
            # 海外同比：[-20%, +120%] → [0, 1]
            parts.append(_norm_high_better(overseas_yoy, -0.2, 1.2))

    return _clamp(_avg(parts))


def compute_valuation_score(metrics: MetricsSchema) -> Optional[float]:
    """估影子分（0-1）。越低估越好。"""
    parts: list[Optional[float]] = []

    pe_pct = (
        metrics.valuation.pe_pct_5y
        or metrics.valuation.pe_pct_3y
        or metrics.valuation.pe_pct_10y
    )
    if pe_pct is not None:
        # PE 分位是百分数（0-100），先 /100 转小数
        pct = pe_pct / 100.0 if pe_pct > 1 else pe_pct
        # 分位 [0, 100] → [1, 0]
        parts.append(_norm_low_better(pct, 0.0, 1.0))

    pb_pct = (
        metrics.valuation.pb_pct_5y
        or metrics.valuation.pb_pct_3y
        or metrics.valuation.pb_pct_10y
    )
    if pb_pct is not None:
        pct = pb_pct / 100.0 if pb_pct > 1 else pb_pct
        parts.append(_norm_low_better(pct, 0.0, 1.0))

    pe_ttm = metrics.valuation.pe_ttm
    if pe_ttm is not None and pe_ttm > 0:
        # PE-TTM [5, 60] → [1, 0]
        parts.append(_norm_low_better(pe_ttm, 5.0, 60.0))

    return _clamp(_avg(parts))


def compute_quality_score(metrics: MetricsSchema) -> Optional[float]:
    """质量子分（0-1）。"""
    parts: list[Optional[float]] = []

    gm = metrics.quality.gross_margin
    if gm is not None:
        # 毛利率通常 0-1，[10%, 80%] → [0, 1]
        parts.append(_norm_high_better(gm, 0.10, 0.80))

    ocf = metrics.quality.ocf_to_net_profit
    if ocf is not None:
        # OCF/净利润 [0, 1.5] → [0, 1]
        parts.append(_norm_high_better(ocf, 0.0, 1.5))

    debt = metrics.quality.debt_ratio
    if debt is not None:
        # 资产负债率越低越好 [20%, 80%] → [1, 0]
        parts.append(_norm_low_better(debt, 0.20, 0.80))

    return _clamp(_avg(parts))


def compute_catalyst_score(metrics: MetricsSchema) -> Optional[float]:
    """催化子分（0-1）。"""
    parts: list[Optional[float]] = []

    y1 = metrics.catalyst.eps_y1_growth
    if y1 is not None:
        # Y1 增速 [-20%, +80%] → [0, 1]
        parts.append(_norm_high_better(y1, -0.2, 0.8))

    y2 = metrics.catalyst.eps_y2_growth
    if y2 is not None:
        parts.append(_norm_high_better(y2, -0.2, 0.8))

    reports = metrics.catalyst.reports_count_90d
    if reports is not None:
        # 研报覆盖数 [0, 10] → [0, 1]：覆盖越多催化越强
        parts.append(_norm_high_better(float(reports), 0.0, 10.0))

    return _clamp(_avg(parts))


def compute_neglect_score(metrics: MetricsSchema) -> Optional[float]:
    """被忽视子分（0-1）。研报少 / 新闻少 / 非 AI = 高分。"""
    parts: list[Optional[float]] = []

    reports = metrics.catalyst.reports_count_90d
    if reports is not None:
        # 研报数 [0, 10] → [1, 0]：覆盖越少越"被忽视"
        parts.append(_norm_low_better(float(reports), 0.0, 10.0))

    news = metrics.catalyst.news_count_30d
    if news is not None:
        # 新闻数 [0, 50] → [1, 0]
        parts.append(_norm_low_better(float(news), 0.0, 50.0))

    is_ai = metrics.catalyst.is_ai_related
    if is_ai is not None:
        # 非 AI 概念 = 1（被忽视），AI 概念 = 0（已被关注）
        parts.append(0.0 if is_ai else 1.0)

    return _clamp(_avg(parts))


def compute_risk_penalty(metrics: MetricsSchema) -> Optional[float]:
    """风险扣分（0-1）。parse_warning + 低现金流 + 高负债累加，封顶 1.0。"""
    if metrics.overseas.parse_warning:
        # 海外收入解析有 warning：固定扣 0.3
        return _clamp(0.3)
    debt = metrics.quality.debt_ratio
    ocf = metrics.quality.ocf_to_net_profit
    penalty = 0.0
    if debt is not None and debt > 0.7:
        penalty += 0.2
    if ocf is not None and ocf < 0.3:
        penalty += 0.2
    return _clamp(penalty) if penalty > 0 else 0.0


def compute_score(
    metrics: MetricsSchema,
    strategy: str,
    weights: Optional[dict[str, float]] = None,
) -> ScoreMetrics:
    """计算子分 + final_score，写入新的 ScoreMetrics。

    Args:
        metrics: 已填充 valuation/growth/quality/overseas/catalyst 的指标
        strategy: "consumer" / "overseas"，决定默认权重和 neglect 是否参与
        weights: 自定义权重（覆盖默认）。None 时用 default_weights(strategy)

    Returns:
        ScoreMetrics，子分 + final_score 都已填好
    """
    w = weights if weights is not None else default_weights(strategy)

    growth = compute_growth_score(metrics, strategy)
    valuation = compute_valuation_score(metrics)
    quality = compute_quality_score(metrics)
    catalyst = compute_catalyst_score(metrics)
    neglect = compute_neglect_score(metrics)
    risk = compute_risk_penalty(metrics)

    # 加权求和：缺失项的权重重新归一化到剩余项
    subscores = {
        "growth": (growth, w.get("growth", 0.0)),
        "valuation": (valuation, w.get("valuation", 0.0)),
        "quality": (quality, w.get("quality", 0.0)),
        "catalyst": (catalyst, w.get("catalyst", 0.0)),
        "neglect": (neglect, w.get("neglect", 0.0)),
    }
    total_w = 0.0
    weighted_sum = 0.0
    for score_val, weight_val in subscores.values():
        if score_val is None or weight_val <= 0:
            continue
        weighted_sum += score_val * weight_val
        total_w += weight_val
    base = weighted_sum / total_w if total_w > 0 else None
    final_score = (
        base - (risk or 0.0) * DEFAULT_RISK_PENALTY_WEIGHT
        if base is not None else None
    )
    if final_score is not None:
        final_score = _clamp(final_score)

    return ScoreMetrics(
        growth_score=growth,
        valuation_score=valuation,
        quality_score=quality,
        catalyst_score=catalyst,
        neglect_score=neglect,
        risk_penalty=risk,
        final_score=final_score,
        weights_used=dict(w),
    )
