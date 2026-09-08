"""系统工具：确定性计算，不访问外部 API。"""

from agent_service.tools.system.bindings import SYSTEM_TOOLS, compute_metrics
from agent_service.tools.system.compute import run_compute_metrics
from agent_service.tools.system.metrics import (
    cagr,
    log_return_vol,
    price_return,
    range_percentile,
)
from agent_service.tools.system.models import (
    ComputeMetricsData,
    MetricOp,
    MetricResult,
    SeriesPoint,
)

__all__ = [
    "SYSTEM_TOOLS",
    "ComputeMetricsData",
    "MetricOp",
    "MetricResult",
    "SeriesPoint",
    "cagr",
    "compute_metrics",
    "log_return_vol",
    "price_return",
    "range_percentile",
    "run_compute_metrics",
]
