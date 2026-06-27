"""metrics_json 和 config_json 的 schema 定义。

约定（参见 IMPROVEMENTS P0-3 / P0-4）：
- 未计算的字段写 None，不写伪造的 0。
- 数据源状态走 source_status，不要散落在主指标里。
- 低可信解析或估算必须写 parse_warning / data_warning。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class ValuationMetrics:
    pe_ttm: Optional[float] = None
    pe_pct_3y: Optional[float] = None
    pe_pct_5y: Optional[float] = None
    pb: Optional[float] = None
    pb_pct_3y: Optional[float] = None
    market_cap_yi: Optional[float] = None  # 市值（亿元）


@dataclass
class GrowthMetrics:
    revenue_yoy: Optional[float] = None
    revenue_ttm_yoy: Optional[float] = None
    deducted_profit_yoy_ttm: Optional[float] = None
    net_profit_yoy: Optional[float] = None


@dataclass
class QualityMetrics:
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None
    roe: Optional[float] = None
    ocf_to_net_profit: Optional[float] = None
    debt_ratio: Optional[float] = None


@dataclass
class OverseasMetrics:
    overseas_ratio: Optional[float] = None
    overseas_yoy: Optional[float] = None
    overseas_revenue_yi: Optional[float] = None
    parse_warning: Optional[str] = None  # 单位识别疑点、跨页断字等


@dataclass
class CatalystMetrics:
    reports_count_90d: Optional[int] = None
    hot_reason_count_30d: Optional[int] = None
    news_count_30d: Optional[int] = None


@dataclass
class SourceStatus:
    """数据源拉取状态：ok / missing / skipped / error。"""

    financials: str = "ok"
    valuation: str = "ok"
    annual_pdf: str = "ok"
    overseas_parser: str = "ok"
    consensus: str = "skipped"
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class MetricsSchema:
    valuation: ValuationMetrics = field(default_factory=ValuationMetrics)
    growth: GrowthMetrics = field(default_factory=GrowthMetrics)
    quality: QualityMetrics = field(default_factory=QualityMetrics)
    overseas: OverseasMetrics = field(default_factory=OverseasMetrics)
    catalyst: CatalystMetrics = field(default_factory=CatalystMetrics)
    source_status: SourceStatus = field(default_factory=SourceStatus)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


@dataclass
class Thresholds:
    pe_ttm_max: Optional[float] = None
    pe_percentile_max: Optional[float] = None
    deducted_yoy_min: Optional[float] = None
    overseas_ratio_min: Optional[float] = None
    overseas_yoy_min: Optional[float] = None
    cashflow_quality_min: Optional[float] = None
    debt_ratio_max: Optional[float] = None


@dataclass
class DataSources:
    valuation_history: str = "akshare.stock_value_em"
    valuation_snapshot: str = "tencent_quote"
    financials: str = "sina+akshare"
    reports: str = "eastmoney"
    announcements: str = "cninfo"


@dataclass
class RuntimeConfig:
    max_workers: int = 1
    non_em_max_workers: int = 2
    single_request_timeout: int = 30
    single_stock_timeout: int = 300
    run_timeout: int = 4 * 3600
    retry_times: int = 2
    resume: bool = False


@dataclass
class ConfigSchema:
    framework_version: str = "0.1.0"
    strategy: str = ""
    period: str = ""
    thresholds: Thresholds = field(default_factory=Thresholds)
    data_sources: DataSources = field(default_factory=DataSources)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    score_weights: Optional[dict[str, float]] = None  # P2 评分层启用后写入

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("score_weights") is None:
            d.pop("score_weights", None)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    def fingerprint(self) -> str:
        """用于 --resume 判断配置是否变化。

        排除 runtime（max_workers 等不影响结果），只看会影响命中清单的字段。
        """
        d = {
            "framework_version": self.framework_version,
            "strategy": self.strategy,
            "period": self.period,
            "thresholds": asdict(self.thresholds),
            "data_sources": asdict(self.data_sources),
        }
        return json.dumps(d, ensure_ascii=False, sort_keys=True, default=str)
