"""给 Agents SDK 用的 `@function_tool` 包装。P3-9 再挂到 Crypto Research Agent。"""

from __future__ import annotations

from agents import RunContextWrapper, Tool, function_tool

from agent_service.schemas.tools import ToolResult
from agent_service.tools.deps import ToolDeps
from agent_service.tools.system.compute import run_compute_metrics
from agent_service.tools.system.models import ComputeMetricsData, SeriesPoint


@function_tool
async def compute_metrics(
    ctx: RunContextWrapper[ToolDeps],
    series: list[SeriesPoint],
    ops: list[str],
    years: float | None = None,
    periods_per_year: float | None = None,
) -> ToolResult[ComputeMetricsData]:
    """用 Python 计算涨跌幅 / CAGR / 波动率 / 区间百分位，不要心算。

    返回的 ratio 是小数：0.15 表示 15%。percentile 是 0–100，表示末值在区间中的位置。

    Args:
        series: 按时间排列的点，每项含 value，可选 timestamp。
        ops: 要算的指标，可多选：return / cagr / volatility / percentile。
        years: 仅 CAGR。序列没有 timestamp 时必须提供，单位年。
        periods_per_year: 仅波动率年化。缺省时按时间戳间隔推断，再不行按 365。
    """
    return run_compute_metrics(
        ctx.context,
        series=series,
        ops=ops,
        years=years,
        periods_per_year=periods_per_year,
    )


SYSTEM_TOOLS: list[Tool] = [compute_metrics]
