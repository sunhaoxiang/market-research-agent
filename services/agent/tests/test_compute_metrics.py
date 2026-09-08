"""compute_metrics（P3-5 验收）。涨跌幅 / CAGR / 波动率 / 百分位由 Python 计算，含边界值。"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from agent_service.providers.runtime import Clock
from agent_service.schemas.tools import ToolErrorCode
from agent_service.tools.deps import ToolDeps
from agent_service.tools.registry import invoke_tool
from agent_service.tools.system.bindings import SYSTEM_TOOLS
from agent_service.tools.system.compute import run_compute_metrics
from agent_service.tools.system.metrics import cagr, log_return_vol, price_return, range_percentile
from agent_service.tools.system.models import ComputeMetricsData, MetricOp, SeriesPoint

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _deps() -> ToolDeps:
    return ToolDeps(clock=Clock.frozen(at=_NOW))


def _pts(*values: float, start: datetime | None = None, step_days: int = 365) -> list[SeriesPoint]:
    points: list[SeriesPoint] = []
    for index, value in enumerate(values):
        stamp = None if start is None else start + timedelta(days=step_days * index)
        points.append(SeriesPoint(value=value, timestamp=stamp))
    return points


def _by_op(data: ComputeMetricsData, op: MetricOp) -> float | None:
    for item in data.results:
        if item.op is op:
            return item.value
    raise AssertionError(op)


def test_price_return_up_and_down() -> None:
    assert price_return(100, 110) == pytest.approx(0.1)
    assert price_return(100, 50) == pytest.approx(-0.5)
    assert price_return(0, 10) is None


def test_cagr_two_years_is_ten_percent() -> None:
    assert cagr(100, 121, 2) == pytest.approx(0.1)
    assert cagr(100, 121, 0) is None
    assert cagr(0, 121, 2) is None
    assert cagr(-10, 10, 2) is None


def test_range_percentile_bounds() -> None:
    values = (10.0, 20.0, 30.0)
    assert range_percentile(values, 10) == 0
    assert range_percentile(values, 20) == 50
    assert range_percentile(values, 30) == 100
    assert range_percentile((7.0, 7.0, 7.0), 7) is None
    assert range_percentile((), 1) is None


def test_vol_known_log_returns() -> None:
    values = (100.0, 100.0 * math.exp(0.1), 100.0 * math.exp(0.4))
    expected = math.sqrt(0.02) * math.sqrt(365)
    assert log_return_vol(values, periods_per_year=365) == pytest.approx(expected)
    assert log_return_vol((100.0, 110.0), periods_per_year=365) is None
    assert log_return_vol((100.0, 0.0, 110.0, 120.0), periods_per_year=365) is None


def test_tool_return_and_percentile() -> None:
    result = run_compute_metrics(
        _deps(),
        series=_pts(10, 20, 30),
        ops=["return", "percentile"],
    )
    assert result.ok is True
    assert result.data is not None
    assert _by_op(result.data, MetricOp.RETURN) == 2.0
    assert _by_op(result.data, MetricOp.PERCENTILE) == 100
    assert result.provenance is not None
    assert result.provenance.provider == "system"
    assert result.provenance.retrieved_at == _NOW


def test_cagr_from_timestamps() -> None:
    start = datetime(2024, 9, 8, tzinfo=UTC)
    series = [
        SeriesPoint(value=100, timestamp=start),
        SeriesPoint(value=121, timestamp=start + timedelta(days=730)),
    ]
    result = run_compute_metrics(_deps(), series=series, ops=["cagr"])
    assert result.data is not None
    assert _by_op(result.data, MetricOp.CAGR) == pytest.approx(cagr(100, 121, 730 / 365.25))


def test_cagr_from_years_without_timestamps() -> None:
    result = run_compute_metrics(_deps(), series=_pts(100, 121), ops=["cagr"], years=2)
    assert result.data is not None
    assert _by_op(result.data, MetricOp.CAGR) == pytest.approx(0.1)


def test_cagr_missing_span_is_partial() -> None:
    result = run_compute_metrics(_deps(), series=_pts(100, 121), ops=["cagr", "return"])
    assert result.ok is True
    assert result.data is not None
    assert result.quality is not None
    assert result.quality.completeness == "partial"
    assert "cagr" in result.quality.missing_fields
    assert _by_op(result.data, MetricOp.RETURN) == pytest.approx(0.21)
    assert _by_op(result.data, MetricOp.CAGR) is None


def test_volatility_assumes_365_without_timestamps() -> None:
    values = (100.0, 100.0 * math.exp(0.1), 100.0 * math.exp(0.4))
    result = run_compute_metrics(_deps(), series=_pts(*values), ops=["volatility"])
    assert result.data is not None
    assert _by_op(result.data, MetricOp.VOLATILITY) == pytest.approx(
        log_return_vol(values, periods_per_year=365)
    )
    assert result.quality is not None
    assert any("365" in item for item in result.quality.caveats)


def test_volatility_infers_frequency_from_timestamps() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    values = (100.0, 100.0 * math.exp(0.1), 100.0 * math.exp(0.4))
    series = _pts(*values, start=start, step_days=1)
    result = run_compute_metrics(_deps(), series=series, ops=["volatility"])
    assert result.data is not None
    assert _by_op(result.data, MetricOp.VOLATILITY) == pytest.approx(
        log_return_vol(values, periods_per_year=365.25)
    )


def test_empty_series_is_invalid() -> None:
    result = run_compute_metrics(_deps(), series=[], ops=["return"])
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT


def test_empty_ops_is_invalid() -> None:
    result = run_compute_metrics(_deps(), series=_pts(1, 2), ops=[])
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT


def test_unknown_op_is_invalid() -> None:
    result = run_compute_metrics(_deps(), series=_pts(1, 2), ops=["sharpe"])
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT


def test_non_finite_is_invalid() -> None:
    result = run_compute_metrics(_deps(), series=[SeriesPoint(value=math.inf)], ops=["return"])
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT


def test_single_point_undefined_ops_are_missing() -> None:
    result = run_compute_metrics(
        _deps(), series=_pts(42), ops=["return", "cagr", "volatility", "percentile"]
    )
    assert result.ok is True
    assert result.data is not None
    assert _by_op(result.data, MetricOp.RETURN) == 0.0
    assert result.quality is not None
    assert set(result.quality.missing_fields) == {"cagr", "volatility", "percentile"}


def test_zero_first_return_is_missing() -> None:
    result = run_compute_metrics(_deps(), series=_pts(0, 10), ops=["return"])
    assert result.data is not None
    assert _by_op(result.data, MetricOp.RETURN) is None


def test_sorts_by_timestamp() -> None:
    later = SeriesPoint(value=200, timestamp=_NOW)
    earlier = SeriesPoint(value=100, timestamp=_NOW - timedelta(days=30))
    result = run_compute_metrics(_deps(), series=[later, earlier], ops=["return"])
    assert result.data is not None
    assert result.data.first == 100
    assert result.data.last == 200
    assert _by_op(result.data, MetricOp.RETURN) == 1.0


async def test_invoke_registry() -> None:
    ok = await invoke_tool(
        "compute_metrics",
        {"series": [{"value": 100}, {"value": 110}], "ops": ["return"]},
        _deps(),
    )
    assert ok.ok is True
    assert ok.data is not None
    assert ok.data.results[0].value == pytest.approx(0.1)
    missing = await invoke_tool("compute_metrics", {}, _deps())
    assert missing.ok is False
    assert missing.error is not None
    assert missing.error.code is ToolErrorCode.INVALID_INPUT


def test_function_tool_name_is_stable() -> None:
    assert [tool.name for tool in SYSTEM_TOOLS] == ["compute_metrics"]
