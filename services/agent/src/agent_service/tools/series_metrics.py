"""把 TVL / 价格序列打成 METRIC_FOUND，让图表随工具完成增长。

不让 LLM 抄 30 个点：数字来自 tool 已解析的序列，来源短引用沿用 stamp 的 ref。
本模块由 collector 加载，不能 import defi/crypto bindings（循环依赖），
所以只认字段形状：`series[].tvl_usd` 或 `prices[].price`。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agent_service.schemas.entities import MetricPoint
from agent_service.schemas.events import MetricFoundEvent, MetricFoundPayload

if TYPE_CHECKING:
    from agent_service.schemas.tools import ToolResult
    from agent_service.tools.web.collector import SourceCollector


@runtime_checkable
class _TvlPoint(Protocol):
    timestamp: datetime
    tvl_usd: float


@runtime_checkable
class _TvlData(Protocol):
    symbol: str | None
    protocol: str | None
    chain: str | None
    series: list[_TvlPoint]


@runtime_checkable
class _PricePoint(Protocol):
    timestamp: datetime
    price: float


@runtime_checkable
class _PriceHistory(Protocol):
    coin_id: str
    vs_currency: str
    prices: list[_PricePoint]


def emit_series_metrics[T](collector: SourceCollector, result: ToolResult[T]) -> None:
    """成功的 TVL / 价格历史才发点。invoke 路径没有 collector.bus，自然不发。"""
    bus = collector.registry.bus
    if bus is None or not result.ok or result.data is None or result.ref is None:
        return
    for metric in metrics_from_tool_data(result.data, result.ref):
        bus.emit(MetricFoundEvent, payload=MetricFoundPayload(metric=metric))


def metrics_from_tool_data(data: object, source_ref: str) -> list[MetricPoint]:
    if isinstance(data, _TvlData):
        entity = data.symbol or data.protocol or data.chain
        return [
            MetricPoint(
                name="tvl",
                label="TVL",
                value=point.tvl_usd,
                unit="USD",
                entity_symbol=entity,
                as_of=point.timestamp,
                source_ref=source_ref,
            )
            for point in data.series
            if math.isfinite(point.tvl_usd)
        ]
    if isinstance(data, _PriceHistory):
        unit = data.vs_currency.upper()
        return [
            MetricPoint(
                name="price",
                label="价格",
                value=point.price,
                unit=unit,
                entity_symbol=data.coin_id,
                as_of=point.timestamp,
                source_ref=source_ref,
            )
            for point in data.prices
            if math.isfinite(point.price)
        ]
    return []
