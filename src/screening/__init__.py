"""筛选层公共 API。"""
from .backtest import (
    DEFAULT_WINDOWS,
    compute_forward_returns,
    compute_forward_returns_batch,
    normalize_windows,
    summarize_backtest,
)
from .consistency import (
    ConsistencyObservation,
    ConsistencyResult,
    check_consistency,
    check_consistency_batch,
    check_eps_consistency,
    check_overseas_consistency,
)
from .period import (
    KIND_ANNUAL,
    KIND_HALF_YEAR,
    KIND_Q1,
    KIND_Q3,
    PeriodInfo,
    parse_period,
    require_overseas_filter,
)
from .result import ScreeningResult
from .run_diff import (
    DEFAULT_METRIC_THRESHOLDS,
    DiffEvent,
    RunDiff,
    diff_latest_two_runs,
    diff_runs,
)
from .schemas import (
    CatalystMetrics,
    ConfigSchema,
    DataSources,
    GrowthMetrics,
    MetricsSchema,
    OverseasMetrics,
    QualityMetrics,
    RuntimeConfig,
    ScoreMetrics,
    SourceStatus,
    Thresholds,
    ValuationMetrics,
)
from .scoring import (
    DEFAULT_RISK_PENALTY_WEIGHT,
    DEFAULT_WEIGHTS_CONSUMER,
    DEFAULT_WEIGHTS_OVERSEAS,
    compute_score,
    default_weights,
)
from .status import (
    DATA_MISSING_REASONS,
    REJECT_CONSUMER,
    REJECT_OVERSEAS,
    WATCH_REASONS,
    RunStatus,
    Status,
)

__all__ = [
    "ScreeningResult",
    "Status", "RunStatus",
    "REJECT_CONSUMER", "REJECT_OVERSEAS", "WATCH_REASONS", "DATA_MISSING_REASONS",
    "MetricsSchema", "ConfigSchema", "Thresholds", "DataSources", "RuntimeConfig",
    "ValuationMetrics", "GrowthMetrics", "QualityMetrics", "OverseasMetrics",
    "CatalystMetrics", "SourceStatus", "ScoreMetrics",
    "compute_score", "default_weights",
    "DEFAULT_WEIGHTS_CONSUMER", "DEFAULT_WEIGHTS_OVERSEAS", "DEFAULT_RISK_PENALTY_WEIGHT",
    "PeriodInfo", "parse_period", "require_overseas_filter",
    "KIND_ANNUAL", "KIND_HALF_YEAR", "KIND_Q1", "KIND_Q3",
    "RunDiff", "DiffEvent", "diff_runs", "diff_latest_two_runs",
    "DEFAULT_METRIC_THRESHOLDS",
    "ConsistencyResult", "ConsistencyObservation",
    "check_consistency", "check_consistency_batch",
    "check_eps_consistency", "check_overseas_consistency",
    "DEFAULT_WINDOWS", "compute_forward_returns",
    "compute_forward_returns_batch", "normalize_windows", "summarize_backtest",
]
