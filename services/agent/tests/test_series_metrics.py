"""工具返回的 TVL / 价格序列打成 METRIC_FOUND（P3-11）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_service.observability.event_bus import EventBus
from agent_service.schemas.events import EventType, MetricFoundPayload
from agent_service.schemas.tools import DataProvenance, ToolResult
from agent_service.sources.registry import SourceRegistry
from agent_service.tools.crypto.models import CryptoPriceHistoryData, CryptoPricePoint
from agent_service.tools.defi.models import TvlData, TvlPointData
from agent_service.tools.series_metrics import emit_series_metrics, metrics_from_tool_data
from agent_service.tools.stocks.models import StockBarData, StockPriceHistoryData
from agent_service.tools.web.collector import SourceCollector

_NOW = datetime(2026, 9, 8, tzinfo=UTC)
_LLAMA = "https://defillama.com/protocol/hyperliquid"


def _tvl_series(*, days: int = 31) -> TvlData:
    start = _NOW - timedelta(days=days - 1)
    return TvlData(
        scope="protocol",
        protocol="hyperliquid",
        symbol="HYPE",
        tvl_usd=1_500_000_000.0,
        series=[
            TvlPointData(timestamp=start + timedelta(days=index), tvl_usd=1_400_000_000.0 + index)
            for index in range(days)
        ],
        url=_LLAMA,
    )


def test_tvl_series_becomes_dated_metric_points() -> None:
    points = metrics_from_tool_data(_tvl_series(), "s1")
    assert len(points) == 31
    assert points[0].name == "tvl"
    assert points[0].entity_symbol == "HYPE"
    assert points[0].as_of is not None
    assert points[-1].value == 1_400_000_000.0 + 30
    assert {item.source_ref for item in points} == {"s1"}


def test_price_history_becomes_price_points() -> None:
    data = CryptoPriceHistoryData(
        coin_id="hyperliquid",
        vs_currency="usd",
        days=30,
        prices=[
            CryptoPricePoint(timestamp=_NOW - timedelta(days=30), price=30.0),
            CryptoPricePoint(timestamp=_NOW, price=42.5),
        ],
        url="https://www.coingecko.com/en/coins/hyperliquid",
    )
    points = metrics_from_tool_data(data, "s2")
    assert [item.name for item in points] == ["price", "price"]
    assert points[0].unit == "USD"
    assert points[0].entity_symbol == "hyperliquid"


def test_stock_price_history_becomes_price_points() -> None:
    data = StockPriceHistoryData(
        ticker="NVDA",
        days=30,
        start=_NOW.date() - timedelta(days=30),
        end=_NOW.date(),
        bars=[
            StockBarData(session=(_NOW - timedelta(days=30)).date(), close=100.0),
            StockBarData(session=_NOW.date(), close=120.5),
        ],
        url="https://financialmodelingprep.com/financial-summary/NVDA",
    )
    points = metrics_from_tool_data(data, "s3")
    assert [item.name for item in points] == ["price", "price"]
    assert points[0].unit == "USD"
    assert points[0].entity_symbol == "NVDA"
    assert points[-1].value == 120.5
    assert points[-1].as_of == datetime(_NOW.year, _NOW.month, _NOW.day, tzinfo=UTC)


async def test_emit_series_metrics_sends_metric_found() -> None:
    bus = EventBus("sess-m", heartbeat_interval_s=60.0)
    collector = SourceCollector(registry=SourceRegistry(bus=bus))
    result = ToolResult.success(
        _tvl_series(),
        DataProvenance(provider="defillama", endpoint="/protocol/hyperliquid", retrieved_at=_NOW),
    )
    result = result.model_copy(update={"ref": "s1"})
    emit_series_metrics(collector, result)
    bus.close()
    events = [event async for event in bus.stream()]
    found = [event for event in events if event.type is EventType.METRIC_FOUND]
    assert len(found) == 31
    assert isinstance(found[0].payload, MetricFoundPayload)
    assert found[0].payload.metric.name == "tvl"
