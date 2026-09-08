"""compute_metrics 的结构化输出。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from agent_service.schemas.common import Schema


class MetricOp(StrEnum):
    RETURN = "return"
    CAGR = "cagr"
    VOLATILITY = "volatility"
    PERCENTILE = "percentile"


class SeriesPoint(Schema):
    value: float
    timestamp: datetime | None = None


class MetricResult(Schema):
    op: MetricOp
    value: float | None
    unit: Literal["ratio", "percentile"]
    """ratio：0.15 = 15%。percentile：0–100，末值在 [min, max] 中的位置。"""


class ComputeMetricsData(Schema):
    n: int
    first: float
    last: float
    start: datetime | None = None
    end: datetime | None = None
    results: list[MetricResult] = Field(default_factory=list)
