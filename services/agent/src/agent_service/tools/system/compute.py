"""compute_metrics：把序列变成涨跌幅 / CAGR / 波动率 / 百分位。"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from agent_service.schemas.tools import DataProvenance, DataQuality, ToolResult
from agent_service.tools._result import fail_invalid
from agent_service.tools.deps import ToolDeps
from agent_service.tools.system.metrics import (
    cagr,
    default_periods_per_year,
    infer_periods_per_year,
    log_return_vol,
    price_return,
    range_percentile,
    years_between,
)
from agent_service.tools.system.models import (
    ComputeMetricsData,
    MetricOp,
    MetricResult,
    SeriesPoint,
)

_TOOL = "compute_metrics"
_MAX_POINTS = 10_000
_OPS = {item.value: item for item in MetricOp}


def run_compute_metrics(
    deps: ToolDeps,
    *,
    series: list[SeriesPoint],
    ops: list[str],
    years: float | None = None,
    periods_per_year: float | None = None,
) -> ToolResult[ComputeMetricsData]:
    wanted, error = _check_args(series, ops, years, periods_per_year)
    if error is not None:
        return fail_invalid(_TOOL, error)

    points = _ordered(series)
    values = [point.value for point in points]
    start, end = _window(points)
    caveats: list[str] = []
    results: list[MetricResult] = []

    for op in wanted:
        value, unit, note = _compute(
            op,
            values=values,
            start=start,
            end=end,
            years=years,
            periods_per_year=periods_per_year,
            timestamps=_timestamps(points),
        )
        results.append(MetricResult(op=op, value=value, unit=unit))
        if note is not None:
            caveats.append(note)

    missing = [item.op.value for item in results if item.value is None]
    quality = None
    if missing or caveats:
        quality = DataQuality(
            completeness="partial" if missing else "full",
            missing_fields=missing,
            caveats=caveats,
        )
    return ToolResult.success(
        ComputeMetricsData(
            n=len(values),
            first=values[0],
            last=values[-1],
            start=start,
            end=end,
            results=results,
        ),
        DataProvenance(
            provider="system",
            endpoint=_TOOL,
            retrieved_at=deps.now(),
            as_of=end,
        ),
        quality=quality,
    )


def _check_args(
    series: list[SeriesPoint],
    ops: list[str],
    years: float | None,
    periods_per_year: float | None,
) -> tuple[tuple[MetricOp, ...], str | None]:
    if not series:
        return (), "序列为空"
    if len(series) > _MAX_POINTS:
        return (), f"序列最长 {_MAX_POINTS} 点"
    if any(not math.isfinite(point.value) for point in series):
        return (), "序列含非有限值"
    if years is not None and (years <= 0 or not math.isfinite(years)):
        return (), "years 必须为正数"
    if periods_per_year is not None and (
        periods_per_year <= 0 or not math.isfinite(periods_per_year)
    ):
        return (), "periods_per_year 必须为正数"
    return _parse_ops(ops)


def _parse_ops(ops: list[str]) -> tuple[tuple[MetricOp, ...], str | None]:
    if not ops:
        return (), "ops 为空"
    seen: set[str] = set()
    ordered: list[MetricOp] = []
    for raw in ops:
        key = raw.strip().lower()
        if not key or key in seen:
            continue
        parsed = _OPS.get(key)
        if parsed is None:
            allowed = " / ".join(_OPS)
            return (), f"ops 只能是 {allowed}，收到 {raw!r}"
        seen.add(key)
        ordered.append(parsed)
    if not ordered:
        return (), "ops 为空"
    return tuple(ordered), None


def _ordered(series: list[SeriesPoint]) -> list[SeriesPoint]:
    if all(point.timestamp is not None for point in series):
        return sorted(series, key=_timestamp)
    return list(series)


def _timestamp(point: SeriesPoint) -> datetime:
    stamp = point.timestamp
    if stamp is None:
        raise ValueError("missing timestamp")
    return stamp


def _window(points: list[SeriesPoint]) -> tuple[datetime | None, datetime | None]:
    start = points[0].timestamp
    end = points[-1].timestamp
    return start, end


def _timestamps(points: list[SeriesPoint]) -> tuple[datetime, ...]:
    return tuple(point.timestamp for point in points if point.timestamp is not None)


def _compute(
    op: MetricOp,
    *,
    values: list[float],
    start: datetime | None,
    end: datetime | None,
    years: float | None,
    periods_per_year: float | None,
    timestamps: tuple[datetime, ...],
) -> tuple[float | None, Literal["ratio", "percentile"], str | None]:
    if op is MetricOp.RETURN:
        return price_return(values[0], values[-1]), "ratio", None
    if op is MetricOp.PERCENTILE:
        return range_percentile(values, values[-1]), "percentile", None
    if op is MetricOp.CAGR:
        span = years_between(start, end) if start is not None and end is not None else years
        if span is None:
            return None, "ratio", "CAGR 需要时间跨度：给 timestamp 或 years"
        return cagr(values[0], values[-1], span), "ratio", None
    freq, note = _vol_frequency(periods_per_year, timestamps)
    return log_return_vol(values, periods_per_year=freq), "ratio", note


def _vol_frequency(
    override: float | None, timestamps: tuple[datetime, ...]
) -> tuple[float, str | None]:
    if override is not None:
        return override, None
    inferred = infer_periods_per_year(timestamps)
    if inferred is not None:
        return inferred, None
    return (
        default_periods_per_year(),
        "未提供可用时间戳，波动率按每年 365 个点年化",
    )
