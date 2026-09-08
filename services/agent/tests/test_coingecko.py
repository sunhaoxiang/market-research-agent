"""CoinGecko provider 契约测试（P3-1 验收）。

钉死三件事：key 只走 `x-cg-demo-api-key` 请求头、响应映射（含 200 空
payload = 没这个币）、以及走 BaseProvider 的缓存路径。429 / 5xx 重试是
基类的职责，这里不重复测。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from agent_service.providers.crypto import CoinGeckoProvider
from agent_service.providers.errors import ProviderError
from agent_service.providers.runtime import Clock, ProviderRuntime
from agent_service.schemas.tools import ToolErrorCode

_BASE = "https://api.coingecko.com/api/v3"
_KEY = "CG-test-demo-key"
_HYPE_UPDATED = 1_757_318_400  # 2025-09-08T16:00:00Z


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
async def coingecko(
    runtime: ProviderRuntime, *, api_key: str | None = _KEY
) -> AsyncIterator[CoinGeckoProvider]:
    async with httpx.AsyncClient(base_url=_BASE) as client:
        yield CoinGeckoProvider(runtime=runtime, api_key=api_key, client=client)


def _search_coin(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": "hyperliquid",
        "name": "Hyperliquid",
        "symbol": "HYPE",
        "market_cap_rank": 15,
        "thumb": "https://coin-images.coingecko.com/coins/images/hyperliquid.png",
    }
    body.update(overrides)
    return body


def _search_page(*coins: dict[str, object]) -> dict[str, object]:
    return {"coins": list(coins), "exchanges": [], "icos": [], "categories": [], "nfts": []}


def _price_block(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "usd": 42.5,
        "usd_market_cap": 14_000_000_000,
        "usd_24h_vol": 200_000_000,
        "usd_24h_change": 3.2,
        "last_updated_at": _HYPE_UPDATED,
    }
    body.update(overrides)
    return body


def _market_row(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": "hyperliquid",
        "symbol": "hype",
        "name": "Hyperliquid",
        "current_price": 42.5,
        "market_cap": 14_000_000_000,
        "fully_diluted_valuation": 42_500_000_000,
        "total_volume": 200_000_000,
        "circulating_supply": 333_000_000,
        "total_supply": 1_000_000_000,
        "max_supply": 1_000_000_000,
        "ath": 50.0,
        "ath_date": "2026-09-01T00:00:00.000Z",
        "atl": 1.0,
        "atl_date": "2024-11-29T00:00:00.000Z",
        "high_24h": 43.0,
        "low_24h": 41.0,
        "price_change_percentage_24h": 3.2,
        "last_updated": "2026-09-08T02:00:00.000Z",
    }
    body.update(overrides)
    return body


def _chart(*, prices: list[list[float]] | None = None) -> dict[str, object]:
    return {
        "prices": prices
        if prices is not None
        else [[1_725_148_800_000, 40.0], [1_725_235_200_000, 42.5]],
        "market_caps": [],
        "total_volumes": [],
    }


@respx.mock(base_url=_BASE)
async def test_search_maps_hype_to_hyperliquid(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/search").mock(
        return_value=httpx.Response(200, json=_search_page(_search_coin()))
    )
    async with coingecko(runtime) as provider:
        page = await provider.search_coins("  HYPE  ")

    assert len(page.hits) == 1
    hit = page.hits[0]
    assert hit.id == "hyperliquid"
    assert hit.symbol == "HYPE"
    assert hit.name == "Hyperliquid"
    assert hit.market_cap_rank == 15
    assert hit.url == "https://www.coingecko.com/en/coins/hyperliquid"
    assert page.query == "HYPE"
    assert page.provenance.provider == "coingecko"
    assert page.provenance.endpoint == "/search"


@respx.mock(base_url=_BASE)
async def test_request_puts_key_in_header_not_query(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/search").mock(
        return_value=httpx.Response(200, json=_search_page(_search_coin()))
    )
    async with coingecko(runtime) as provider:
        await provider.search_coins("HYPE")

    sent = route.calls[0].request
    assert sent.headers["x-cg-demo-api-key"] == _KEY
    assert _KEY not in str(sent.url)
    assert "api_key" not in str(sent.url).lower()


@respx.mock(base_url=_BASE)
async def test_blank_key_omits_auth_header(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/search").mock(
        return_value=httpx.Response(200, json=_search_page(_search_coin()))
    )
    async with coingecko(runtime, api_key=None) as provider:
        page = await provider.search_coins("HYPE")

    assert page.hits[0].id == "hyperliquid"
    assert "x-cg-demo-api-key" not in route.calls[0].request.headers


@respx.mock(base_url=_BASE)
async def test_get_price_parses_numbers_and_unix_time(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/simple/price").mock(
        return_value=httpx.Response(200, json={"hyperliquid": _price_block()})
    )
    async with coingecko(runtime) as provider:
        quote = await provider.get_price("Hyperliquid", vs_currency="USD")

    assert quote.coin_id == "hyperliquid"
    assert quote.vs_currency == "usd"
    assert quote.price == pytest.approx(42.5)
    assert quote.market_cap == pytest.approx(14_000_000_000)
    assert quote.volume_24h == pytest.approx(200_000_000)
    assert quote.change_24h_pct == pytest.approx(3.2)
    assert quote.as_of == datetime.fromtimestamp(_HYPE_UPDATED, tz=UTC)
    assert quote.url == "https://www.coingecko.com/en/coins/hyperliquid"
    params = dict(route.calls[0].request.url.params)
    assert params["ids"] == "hyperliquid"
    assert params["vs_currencies"] == "usd"
    assert params["include_last_updated_at"] == "true"


@respx.mock(base_url=_BASE)
async def test_get_market_parses_fdv_supply_and_ath(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/coins/markets").mock(return_value=httpx.Response(200, json=[_market_row()]))
    async with coingecko(runtime) as provider:
        market = await provider.get_market("hyperliquid")

    assert market.symbol == "HYPE"
    assert market.name == "Hyperliquid"
    assert market.current_price == pytest.approx(42.5)
    assert market.fully_diluted_valuation == pytest.approx(42_500_000_000)
    assert market.circulating_supply == pytest.approx(333_000_000)
    assert market.max_supply == pytest.approx(1_000_000_000)
    assert market.ath == pytest.approx(50.0)
    assert market.ath_date == datetime(2026, 9, 1, tzinfo=UTC)
    assert market.atl_date == datetime(2024, 11, 29, tzinfo=UTC)
    assert market.last_updated == datetime(2026, 9, 8, 2, tzinfo=UTC)
    assert market.provenance.endpoint == "/coins/markets"


@respx.mock(base_url=_BASE)
async def test_get_market_chart_parses_millis(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/coins/hyperliquid/market_chart").mock(
        return_value=httpx.Response(200, json=_chart())
    )
    async with coingecko(runtime) as provider:
        chart = await provider.get_market_chart("hyperliquid", days=30)

    assert chart.days == 30
    assert len(chart.prices) == 2
    assert chart.prices[0].timestamp == datetime.fromtimestamp(1_725_148_800, tz=UTC)
    assert chart.prices[0].price == pytest.approx(40.0)
    assert chart.prices[1].price == pytest.approx(42.5)
    assert chart.provenance.endpoint == "/coins/hyperliquid/market_chart"


@respx.mock(base_url=_BASE)
async def test_identical_queries_hit_the_cache(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/simple/price").mock(
        return_value=httpx.Response(200, json={"hyperliquid": _price_block()})
    )
    async with coingecko(runtime) as provider:
        first = await provider.get_price("hyperliquid")
        second = await provider.get_price("HYPERliquid")

    assert route.call_count == 1
    assert first.provenance.is_cached is False
    assert second.provenance.is_cached is True


@respx.mock(base_url=_BASE, assert_all_called=False)
async def test_empty_query_does_not_call_upstream(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/search").mock(
        return_value=httpx.Response(200, json=_search_page(_search_coin()))
    )
    async with coingecko(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.search_coins("   ")
    assert exc.value.code is ToolErrorCode.INVALID_INPUT
    assert route.call_count == 0


@respx.mock(base_url=_BASE, assert_all_called=False)
async def test_empty_coin_id_does_not_call_upstream(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/simple/price").mock(return_value=httpx.Response(200, json={}))
    async with coingecko(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_price("  ")
    assert exc.value.code is ToolErrorCode.INVALID_INPUT
    assert route.call_count == 0


@respx.mock(base_url=_BASE, assert_all_called=False)
async def test_chart_days_out_of_range_does_not_call_upstream(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/coins/hyperliquid/market_chart").mock(
        return_value=httpx.Response(200, json=_chart())
    )
    async with coingecko(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_market_chart("hyperliquid", days=366)
    assert exc.value.code is ToolErrorCode.INVALID_INPUT
    assert route.call_count == 0


@respx.mock(base_url=_BASE)
async def test_empty_price_payload_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/simple/price").mock(return_value=httpx.Response(200, json={}))
    async with coingecko(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_price("not-a-coin")
    assert exc.value.code is ToolErrorCode.NOT_FOUND


@respx.mock(base_url=_BASE)
async def test_empty_markets_payload_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/coins/markets").mock(return_value=httpx.Response(200, json=[]))
    async with coingecko(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_market("not-a-coin")
    assert exc.value.code is ToolErrorCode.NOT_FOUND


@respx.mock(base_url=_BASE)
async def test_http_404_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/coins/not-a-coin/market_chart").mock(
        return_value=httpx.Response(404, json={"error": "coin not found"})
    )
    async with coingecko(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_market_chart("not-a-coin")
    assert exc.value.code is ToolErrorCode.NOT_FOUND
    assert exc.value.status_code == 404


@respx.mock(base_url=_BASE)
async def test_403_is_a_key_error(respx_mock: respx.MockRouter, runtime: ProviderRuntime) -> None:
    respx_mock.get("/simple/price").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"})
    )
    async with coingecko(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_price("hyperliquid")
    assert exc.value.status_code == 403
    assert "API key" in exc.value.message


@respx.mock(base_url=_BASE, assert_all_called=False)
async def test_empty_vs_currency_does_not_call_upstream(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/simple/price").mock(return_value=httpx.Response(200, json={}))
    async with coingecko(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_price("hyperliquid", vs_currency="  ")
    assert exc.value.code is ToolErrorCode.INVALID_INPUT
    assert route.call_count == 0


@respx.mock(base_url=_BASE)
async def test_missing_coins_is_parse_error(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/search").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    async with coingecko(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.search_coins("HYPE")
    assert exc.value.code is ToolErrorCode.PARSE_ERROR


@respx.mock(base_url=_BASE)
async def test_malformed_json_is_parse_error(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/simple/price").mock(return_value=httpx.Response(200, text="not-json"))
    async with coingecko(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_price("hyperliquid")
    assert exc.value.code is ToolErrorCode.PARSE_ERROR


@respx.mock(base_url=_BASE)
async def test_empty_search_results_are_successful(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/search").mock(return_value=httpx.Response(200, json=_search_page()))
    async with coingecko(runtime) as provider:
        page = await provider.search_coins("zzzz-not-a-coin")
    assert page.hits == ()


@respx.mock(base_url=_BASE)
async def test_search_drops_hits_without_id(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/search").mock(
        return_value=httpx.Response(
            200,
            json=_search_page(
                {"name": "no id", "symbol": "X"},
                _search_coin(),
            ),
        )
    )
    async with coingecko(runtime) as provider:
        page = await provider.search_coins("HYPE")
    assert [hit.id for hit in page.hits] == ["hyperliquid"]
