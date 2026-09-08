"""会话级 Source 登记：去重、编号、SOURCE_FOUND（P2-7）。"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_service.observability.event_bus import EventBus
from agent_service.schemas.common import SourceReliability, SourceType
from agent_service.schemas.events import EventType, SourceFoundPayload
from agent_service.schemas.tools import DataProvenance, ToolResult
from agent_service.sources.registry import SourceRegistry
from agent_service.tools.crypto.models import CryptoPriceData
from agent_service.tools.web.collector import SourceCollector, stamp_refs
from agent_service.tools.web.models import WebSearchData, WebSearchHit

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_URL = "https://www.theblock.co/hyperliquid-fee-share"


def _intern(
    registry: SourceRegistry,
    url: str,
    *,
    title: str | None = "Fee share",
    excerpt: str | None = None,
    source_type: SourceType = SourceType.NEWS,
    domain: str | None = None,
) -> str:
    return registry.intern(
        url=url,
        title=title,
        excerpt=excerpt,
        provider="tavily",
        retrieved_at=_NOW,
        source_type=source_type,
        domain=domain,
    ).ref


def test_tracking_params_merge_to_same_ref() -> None:
    registry = SourceRegistry()
    first = _intern(registry, _URL + "?utm_source=twitter")
    second = _intern(registry, _URL + "/")
    assert first == second == "s1"
    sources = registry.sources()
    assert len(sources) == 1
    assert sources[0].url_canonical == "https://theblock.co/hyperliquid-fee-share"
    assert sources[0].reliability is SourceReliability.SECONDARY


def test_second_distinct_url_gets_next_ref() -> None:
    """去重之后编号连续：这就是 Merge 阶段的「重新编号」。"""
    registry = SourceRegistry()
    _intern(registry, _URL)
    _intern(registry, _URL + "?fbclid=1")
    next_ref = _intern(registry, "https://www.sec.gov/Archives/edgar/data/1/a.htm")
    assert next_ref == "s2"
    assert [item.ref for item in registry.sources()] == ["s1", "s2"]
    assert registry.sources()[1].reliability is SourceReliability.PRIMARY


async def test_source_found_emitted_once_per_canonical() -> None:
    bus = EventBus("sess-src", heartbeat_interval_s=60.0)
    registry = SourceRegistry(bus=bus)
    _intern(registry, _URL + "?utm_medium=rss")
    _intern(registry, _URL)
    bus.close()
    events = [event async for event in bus.stream()]
    found = [event for event in events if event.type is EventType.SOURCE_FOUND]
    assert len(found) == 1
    payload = found[0].payload
    assert isinstance(payload, SourceFoundPayload)
    assert payload.source.ref == "s1"
    assert found[0].message == "发现来源：Fee share"


def test_shared_registry_numbers_across_tasks() -> None:
    registry = SourceRegistry()
    first = SourceCollector(registry=registry)
    second = SourceCollector(registry=registry)
    first.add(
        url=_URL,
        title="A",
        excerpt="a",
        provider="tavily",
        retrieved_at=_NOW,
        source_type=SourceType.NEWS,
    )
    second.add(
        url="https://www.coingecko.com/en/coins/hype",
        title="HYPE",
        excerpt="price",
        provider="tavily",
        retrieved_at=_NOW,
    )
    assert [item.ref for item in first.sources()] == ["s1"]
    assert [item.ref for item in second.sources()] == ["s2"]
    assert second.sources()[0].reliability is SourceReliability.AGGREGATOR


def test_stamp_refs_collapses_tracking_variants() -> None:
    collector = SourceCollector()
    page = ToolResult.success(
        WebSearchData(
            query="HYPE",
            hits=[
                WebSearchHit(url=_URL + "?utm_source=x", title="A", snippet="a"),
                WebSearchHit(url=_URL, title="A", snippet="a"),
            ],
            topic="news",
        ),
        provenance=DataProvenance(provider="tavily", endpoint="/search", retrieved_at=_NOW),
    )
    stamped = stamp_refs(collector, page)
    assert stamped.data is not None
    assert stamped.data.hits[0].ref == stamped.data.hits[1].ref == "s1"
    assert len(collector.sources()) == 1


def test_stamp_refs_registers_structured_api_url() -> None:
    collector = SourceCollector()
    page = "https://www.coingecko.com/en/coins/hyperliquid"
    result = ToolResult.success(
        CryptoPriceData(coin_id="hyperliquid", vs_currency="usd", price=42.5, url=page),
        provenance=DataProvenance(
            provider="coingecko", endpoint="/simple/price", retrieved_at=_NOW
        ),
    )
    stamped = stamp_refs(collector, result)
    assert stamped.ref == "s1"
    assert collector.sources()[0].url == page
    assert collector.sources()[0].source_type is SourceType.API
    assert collector.sources()[0].reliability is SourceReliability.AGGREGATOR
