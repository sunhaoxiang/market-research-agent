"""Tavily SearchProvider 契约测试（P2-2 验收）。

钉死三件事：请求形状（Bearer、不把 key 放 body、search_depth=basic）、
响应映射、以及走 BaseProvider 的缓存/4xx 路径。换 Exa 时这组断言是对照。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import respx

from agent_service.providers.errors import ProviderError
from agent_service.providers.runtime import Clock, ProviderRuntime
from agent_service.providers.search import (
    SearchProvider,
    SearchQuery,
    SearchTimeRange,
    SearchTopic,
    TavilySearchProvider,
)
from agent_service.schemas.tools import ToolErrorCode

_BASE = "https://api.tavily.com"
_KEY = "tvly-test-key"


@pytest.fixture
async def runtime(tmp_path: Path) -> AsyncIterator[ProviderRuntime]:
    clock = Clock.frozen()

    async def sleep(seconds: float) -> None:
        clock.advance_ms(int(seconds * 1000))

    rt = ProviderRuntime(tmp_path / "cache.db", clock=clock, sleep=sleep)
    await rt.open()
    yield rt
    await rt.aclose()


@asynccontextmanager
async def tavily(runtime: ProviderRuntime) -> AsyncIterator[TavilySearchProvider]:
    async with httpx.AsyncClient(base_url=_BASE) as client:
        yield TavilySearchProvider(runtime=runtime, api_key=_KEY, client=client)


def _result(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "title": "Hyperliquid docs",
        "url": "https://www.hyperliquid.xyz/docs",
        "content": "HYPE is the native token.",
        "score": 0.81,
        "raw_content": "# Hyperliquid\n\nNative token HYPE.",
        "published_date": "2026-09-01T12:00:00Z",
    }
    body.update(overrides)
    return body


def _page(*results: dict[str, object]) -> dict[str, object]:
    return {
        "query": "HYPE token",
        "results": list(results),
        "images": [],
        "answer": None,
        "response_time": 0.4,
    }


@respx.mock(base_url=_BASE)
async def test_search_maps_tavily_payload(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.post("/search").mock(return_value=httpx.Response(200, json=_page(_result())))
    async with tavily(runtime) as provider:
        assert isinstance(provider, SearchProvider)
        page = await provider.search(SearchQuery(query="  HYPE token  "))

    assert len(page.hits) == 1
    hit = page.hits[0]
    assert hit.url == "https://www.hyperliquid.xyz/docs"
    assert hit.domain == "hyperliquid.xyz"
    assert hit.title == "Hyperliquid docs"
    assert hit.snippet == "HYPE is the native token."
    assert hit.raw_content is not None and hit.raw_content.startswith("# Hyperliquid")
    assert hit.score == pytest.approx(0.81)
    assert hit.published_at is not None
    assert page.provenance.provider == "tavily"
    assert page.provenance.endpoint == "/search"
    assert page.query == "HYPE token"


@respx.mock(base_url=_BASE)
async def test_request_shape_hides_the_key_and_locks_basic_depth(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.post("/search").mock(return_value=httpx.Response(200, json=_page(_result())))
    async with tavily(runtime) as provider:
        await provider.search(
            SearchQuery(
                query="HYPE",
                max_results=20,
                time_range=SearchTimeRange.WEEK,
                include_domains=("WWW.Example.com", "docs.example.com", "example.com"),
                topic=SearchTopic.NEWS,
            )
        )

    sent = json.loads(route.calls[0].request.content.decode())
    assert "api_key" not in sent
    assert sent["search_depth"] == "basic"
    assert sent["include_answer"] is False
    assert sent["include_raw_content"] == "markdown"
    assert sent["max_results"] == 10  # 上限，避免一次搜把月配额打穿
    assert sent["topic"] == "news"
    assert sent["time_range"] == "week"
    assert sent["include_domains"] == ["docs.example.com", "example.com"]
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {_KEY}"


@respx.mock(base_url=_BASE)
async def test_identical_queries_hit_the_cache(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.post("/search").mock(return_value=httpx.Response(200, json=_page(_result())))
    async with tavily(runtime) as provider:
        first = await provider.search(SearchQuery(query="HYPE"))
        second = await provider.search(SearchQuery(query="HYPE"))

    assert route.call_count == 1
    assert first.provenance.is_cached is False
    assert second.provenance.is_cached is True


@respx.mock(base_url=_BASE)
async def test_domain_filter_order_does_not_split_the_cache(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.post("/search").mock(return_value=httpx.Response(200, json=_page(_result())))
    async with tavily(runtime) as provider:
        await provider.search(SearchQuery(query="HYPE", include_domains=("b.com", "a.com")))
        await provider.search(SearchQuery(query="HYPE", include_domains=("a.com", "b.com")))
    assert route.call_count == 1


@respx.mock(base_url=_BASE, assert_all_called=False)
async def test_empty_query_does_not_call_upstream(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.post("/search").mock(return_value=httpx.Response(200, json=_page(_result())))
    async with tavily(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.search(SearchQuery(query="   "))
    assert exc.value.code is ToolErrorCode.INVALID_INPUT
    assert route.call_count == 0


@respx.mock(base_url=_BASE)
async def test_401_is_a_key_error(respx_mock: respx.MockRouter, runtime: ProviderRuntime) -> None:
    respx_mock.post("/search").mock(return_value=httpx.Response(401, json={"detail": {}}))
    async with tavily(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.search(SearchQuery(query="HYPE"))
    assert exc.value.status_code == 401
    assert "API key" in exc.value.message


@respx.mock(base_url=_BASE)
async def test_missing_results_is_parse_error(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.post("/search").mock(return_value=httpx.Response(200, json={"query": "HYPE"}))
    async with tavily(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.search(SearchQuery(query="HYPE"))
    assert exc.value.code is ToolErrorCode.PARSE_ERROR


@respx.mock(base_url=_BASE)
async def test_hits_without_url_are_dropped(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.post("/search").mock(
        return_value=httpx.Response(
            200,
            json=_page(
                {"title": "no url", "content": "x"},
                _result(url="https://example.com/ok"),
            ),
        )
    )
    async with tavily(runtime) as provider:
        page = await provider.search(SearchQuery(query="HYPE"))
    assert [hit.url for hit in page.hits] == ["https://example.com/ok"]


@respx.mock(base_url=_BASE)
async def test_empty_results_are_a_successful_empty_page(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.post("/search").mock(return_value=httpx.Response(200, json=_page()))
    async with tavily(runtime) as provider:
        page = await provider.search(SearchQuery(query="HYPE"))
    assert page.hits == ()


def test_blank_api_key_is_rejected(tmp_path: Path) -> None:
    runtime = ProviderRuntime(tmp_path / "c.db")
    with pytest.raises(ProviderError) as exc:
        TavilySearchProvider(runtime=runtime, api_key="  ")
    assert exc.value.code is ToolErrorCode.INVALID_INPUT
