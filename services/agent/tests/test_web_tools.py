"""web_* tool：ProviderError 必须收成 ToolResult，provenance 从这一层就带上。"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_service.providers.errors import ProviderError
from agent_service.providers.fetch import FetchedPage
from agent_service.providers.runtime import Clock
from agent_service.providers.search import (
    SearchHit,
    SearchPage,
    SearchQuery,
    SearchTimeRange,
    SearchTopic,
)
from agent_service.schemas.tools import DataProvenance, ToolErrorCode
from agent_service.tools.deps import ToolDeps
from agent_service.tools.registry import invoke_tool
from agent_service.tools.web.bindings import WEB_TOOLS
from agent_service.tools.web.fetch import run_web_fetch
from agent_service.tools.web.search import parse_since, run_news_search, run_web_search

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _prov(**overrides: object) -> DataProvenance:
    body: dict[str, object] = {
        "provider": "tavily",
        "endpoint": "/search",
        "retrieved_at": _NOW,
        "is_cached": False,
    }
    body.update(overrides)
    return DataProvenance.model_validate(body)


def _hit() -> SearchHit:
    return SearchHit(
        url="https://www.hyperliquid.xyz/docs",
        title="Hyperliquid docs",
        snippet="HYPE is the native token.",
        raw_content="# HYPE",
        score=0.8,
        published_at=datetime(2026, 9, 1, tzinfo=UTC),
        domain="hyperliquid.xyz",
    )


class FakeSearch:
    name = "fake"

    def __init__(self, page: SearchPage | None = None, error: ProviderError | None = None) -> None:
        self.page = page
        self.error = error
        self.queries: list[SearchQuery] = []

    async def search(self, query: SearchQuery) -> SearchPage:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        assert self.page is not None
        return self.page

    async def aclose(self) -> None:
        return None


class FakeFetcher:
    def __init__(self, page: FetchedPage | None = None, error: ProviderError | None = None) -> None:
        self.page = page
        self.error = error
        self.urls: list[str] = []

    async def fetch(self, url: str) -> FetchedPage:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        assert self.page is not None
        return self.page


async def test_web_search_success_carries_provenance() -> None:
    search = FakeSearch(
        SearchPage(query="HYPE", hits=(_hit(),), provenance=_prov(is_cached=True, cache_age_s=12))
    )
    result = await run_web_search(
        ToolDeps(search=search),
        query="HYPE",
        max_results=3,
        time_range="week",
        include_domains=["Hyperliquid.xyz"],
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.hits[0].url == "https://www.hyperliquid.xyz/docs"
    assert result.provenance is not None
    assert result.provenance.provider == "tavily"
    assert result.provenance.is_cached is True
    query = search.queries[0]
    assert query.max_results == 3
    assert query.time_range is not None
    assert query.time_range.value == "week"
    assert query.include_domains == ("Hyperliquid.xyz",)
    assert query.topic is SearchTopic.GENERAL


async def test_web_search_empty_query_is_invalid() -> None:
    result = await run_web_search(ToolDeps(search=FakeSearch()), query="  ")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT


async def test_web_search_missing_provider() -> None:
    result = await run_web_search(ToolDeps(), query="HYPE")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UPSTREAM_ERROR
    assert "TAVILY_API_KEY" in result.error.message


async def test_web_search_maps_provider_error() -> None:
    search = FakeSearch(
        error=ProviderError(ToolErrorCode.QUOTA_EXHAUSTED, "本月额度用尽", provider="tavily")
    )
    result = await run_web_search(ToolDeps(search=search), query="HYPE")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.QUOTA_EXHAUSTED
    assert result.error.tool == "web_search"
    assert result.error.provider == "tavily"


async def test_web_search_empty_hits_are_partial() -> None:
    search = FakeSearch(SearchPage(query="zzz", hits=(), provenance=_prov()))
    result = await run_web_search(ToolDeps(search=search), query="zzz")
    assert result.ok is True
    assert result.quality is not None
    assert result.quality.completeness == "partial"
    assert "hits" in result.quality.missing_fields


async def test_web_search_rejects_bad_time_range() -> None:
    result = await run_web_search(
        ToolDeps(search=FakeSearch()), query="HYPE", time_range="yesterday"
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT


async def test_web_search_clamps_max_results() -> None:
    search = FakeSearch(SearchPage(query="HYPE", hits=(), provenance=_prov()))
    await run_web_search(ToolDeps(search=search), query="HYPE", max_results=99)
    assert search.queries[0].max_results == 10


async def test_news_search_uses_news_topic_and_symbols() -> None:
    search = FakeSearch(SearchPage(query="listing HYPE", hits=(_hit(),), provenance=_prov()))
    result = await run_news_search(
        ToolDeps(search=search, clock=Clock.frozen(_NOW)),
        query="listing",
        symbols=["HYPE", "HYPE"],
        since="week",
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.topic == "news"
    assert result.data.symbols == ["HYPE"]
    query = search.queries[0]
    assert query.topic is SearchTopic.NEWS
    assert query.query == "listing HYPE"
    assert query.time_range is not None
    assert query.time_range.value == "week"


async def test_news_search_maps_iso_since() -> None:
    search = FakeSearch(SearchPage(query="HYPE", hits=(), provenance=_prov()))
    await run_news_search(
        ToolDeps(search=search, clock=Clock.frozen(_NOW)),
        query="HYPE",
        since="2026-09-07T00:00:00Z",
    )
    assert search.queries[0].time_range is not None
    assert search.queries[0].time_range.value == "day"


def test_parse_since_windows() -> None:
    assert parse_since("2026-09-01", now=_NOW) is SearchTimeRange.WEEK
    assert parse_since("2026-08-15", now=_NOW) is SearchTimeRange.MONTH
    assert parse_since("2026-01-01", now=_NOW) is SearchTimeRange.YEAR


async def test_news_search_rejects_future_since() -> None:
    result = await run_news_search(
        ToolDeps(search=FakeSearch(), clock=Clock.frozen(_NOW)),
        query="HYPE",
        since="2026-12-01",
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT


async def test_web_fetch_success_and_missing_text() -> None:
    page = FetchedPage(
        url="https://example.test/hype",
        final_url="https://example.test/hype",
        title="HYPE",
        text=None,
        status_code=200,
        content_type="text/html",
        provenance=_prov(provider="web_fetch", endpoint="https://example.test/hype"),
    )
    result = await run_web_fetch(
        ToolDeps(fetcher=FakeFetcher(page)), url="https://example.test/hype"
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.text is None
    assert result.quality is not None
    assert result.quality.missing_fields == ["text"]
    assert result.provenance is not None
    assert result.provenance.provider == "web_fetch"


async def test_web_fetch_maps_blocked() -> None:
    fetcher = FakeFetcher(
        error=ProviderError(ToolErrorCode.BLOCKED, "拒绝内网地址", provider="web_fetch")
    )
    result = await run_web_fetch(ToolDeps(fetcher=fetcher), url="http://127.0.0.1/")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.BLOCKED
    assert result.error.tool == "web_fetch"


async def test_web_fetch_empty_url() -> None:
    result = await run_web_fetch(ToolDeps(fetcher=FakeFetcher()), url="  ")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT


async def test_invoke_dispatches_and_validates() -> None:
    search = FakeSearch(SearchPage(query="HYPE", hits=(_hit(),), provenance=_prov()))
    deps = ToolDeps(search=search)
    ok = await invoke_tool("web_search", {"query": "HYPE"}, deps)
    assert ok.ok is True
    missing = await invoke_tool("web_search", {}, deps)
    assert missing.ok is False
    assert missing.error is not None
    assert missing.error.code is ToolErrorCode.INVALID_INPUT


def test_function_tools_expose_stable_names() -> None:
    assert [tool.name for tool in WEB_TOOLS] == ["web_search", "news_search", "web_fetch"]
