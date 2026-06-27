"""ScreeningResult：单只股票的策略评估结果（P0-2 / P0-5 / P0-6）。

承载 status、原因码、metrics，可序列化为 candidate_scores 表行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .schemas import MetricsSchema
from .status import Status


@dataclass
class ScreeningResult:
    run_id: str
    code: str
    strategy: str
    period: str
    status: Status
    name: Optional[str] = None
    hit_reason: Optional[str] = None
    reject_reason: Optional[str] = None
    watch_reason: Optional[str] = None
    data_missing_reason: Optional[str] = None
    error: Optional[str] = None
    metrics: MetricsSchema = field(default_factory=MetricsSchema)
    created_at: Optional[pd.Timestamp] = None

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = pd.Timestamp.now()
        self._validate_consistency()

    def _validate_consistency(self) -> None:
        """状态与原因码的一致性校验，违反时抛 ValueError。

        用于早暴露策略实现 bug，避免错误状态落库。
        """
        if self.status == Status.HIT and not self.hit_reason:
            raise ValueError(
                f"hit 状态必须有 hit_reason (code={self.code})"
            )
        if self.status == Status.REJECTED and not self.reject_reason:
            raise ValueError(
                f"rejected 状态必须有 reject_reason (code={self.code})"
            )
        if self.status == Status.WATCH and not self.watch_reason:
            raise ValueError(
                f"watch 状态必须有 watch_reason (code={self.code})"
            )
        if self.status == Status.DATA_MISSING and not self.data_missing_reason:
            raise ValueError(
                f"data_missing 状态必须有 data_missing_reason (code={self.code})"
            )
        if self.status == Status.ERROR and not self.error:
            raise ValueError(
                f"error 状态必须有 error message (code={self.code})"
            )

    def to_row(self) -> dict:
        """转 candidate_scores 行格式（供 DuckDBStore.save_candidate_score）。"""
        return {
            "run_id": self.run_id,
            "code": self.code,
            "name": self.name,
            "strategy": self.strategy,
            "period": self.period,
            "status": self.status.value,
            "hit_reason": self.hit_reason,
            "reject_reason": self.reject_reason,
            "data_missing_reason": self.data_missing_reason,
            "metrics_json": self.metrics.to_json(),
            "created_at": self.created_at,
        }

    # === 工厂方法 ===
    @classmethod
    def hit(
        cls,
        *,
        run_id: str,
        code: str,
        strategy: str,
        period: str,
        hit_reason: str,
        name: Optional[str] = None,
        metrics: Optional[MetricsSchema] = None,
    ) -> "ScreeningResult":
        return cls(
            run_id=run_id, code=code, name=name,
            strategy=strategy, period=period,
            status=Status.HIT, hit_reason=hit_reason,
            metrics=metrics or MetricsSchema(),
        )

    @classmethod
    def rejected(
        cls,
        *,
        run_id: str,
        code: str,
        strategy: str,
        period: str,
        reject_reason: str,
        name: Optional[str] = None,
        metrics: Optional[MetricsSchema] = None,
    ) -> "ScreeningResult":
        return cls(
            run_id=run_id, code=code, name=name,
            strategy=strategy, period=period,
            status=Status.REJECTED, reject_reason=reject_reason,
            metrics=metrics or MetricsSchema(),
        )

    @classmethod
    def watch(
        cls,
        *,
        run_id: str,
        code: str,
        strategy: str,
        period: str,
        watch_reason: str,
        name: Optional[str] = None,
        metrics: Optional[MetricsSchema] = None,
    ) -> "ScreeningResult":
        return cls(
            run_id=run_id, code=code, name=name,
            strategy=strategy, period=period,
            status=Status.WATCH, watch_reason=watch_reason,
            metrics=metrics or MetricsSchema(),
        )

    @classmethod
    def data_missing(
        cls,
        *,
        run_id: str,
        code: str,
        strategy: str,
        period: str,
        data_missing_reason: str,
        name: Optional[str] = None,
        metrics: Optional[MetricsSchema] = None,
    ) -> "ScreeningResult":
        return cls(
            run_id=run_id, code=code, name=name,
            strategy=strategy, period=period,
            status=Status.DATA_MISSING,
            data_missing_reason=data_missing_reason,
            metrics=metrics or MetricsSchema(),
        )

    @classmethod
    def from_exception(
        cls,
        *,
        run_id: str,
        code: str,
        strategy: str,
        period: str,
        error: str,
        name: Optional[str] = None,
        metrics: Optional[MetricsSchema] = None,
    ) -> "ScreeningResult":
        return cls(
            run_id=run_id, code=code, name=name,
            strategy=strategy, period=period,
            status=Status.ERROR, error=error,
            metrics=metrics or MetricsSchema(),
        )
