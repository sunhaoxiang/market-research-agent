"""DefiLlama provider 契约测试（P3-2 验收）。

钉死三件事：无鉴权头、响应映射（400/404/空列表 = 没有这项数据）、
以及走 BaseProvider 的缓存路径。days 在本地裁切，不打进 query。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from agent_service.providers.defi import DefiLlamaProvider
from agent_service.providers.errors import ProviderError
from agent_service.providers.runtime import Clock, ProviderRuntime
from agent_service.schemas.tools import ToolErrorCode

_BASE = "https://api.llama.fi"
_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
async def runtime(tmp_path: Path) -> AsyncIterator[ProviderRuntime]:
    clock = Clock.frozen(at=_NOW)

    async def sleep(seconds: float) -> None:
        clock.advance_ms(int(seconds * 1000))

    rt = ProviderRuntime(tmp_path / "cache.db", clock=clock, sleep=sleep)
    await rt.open()
    yield rt
    await rt.aclose()


@asynccontextmanager
async def llama(runtime: ProviderRuntime) -> AsyncIterator[DefiLlamaProvider]:
    async with httpx.AsyncClient(base_url=_BASE) as client:
        yield DefiLlamaProvider(runtime=runtime, client=client)


def _ts(when: datetime) -> int:
    return int(when.timestamp())


def _protocol(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": "hyperliquid",
        "name": "Hyperliquid",
        "symbol": "HYPE",
        "category": "Derivatives",
        "chains": ["Hyperliquid"],
        "currentChainTvls": {"Hyperliquid": 1_500_000_000, "staking": 200_000_000},
        "tvl": [
            {"date": _ts(datetime(2026, 7, 1, tzinfo=UTC)), "totalLiquidityUSD": 800_000_000},
            {"date": _ts(datetime(2026, 9, 1, tzinfo=UTC)), "totalLiquidityUSD": 1_400_000_000},
            {"date": _ts(datetime(2026, 9, 8, tzinfo=UTC)), "totalLiquidityUSD": 1_500_000_000},
        ],
    }
    body.update(overrides)
    return body


def _fees(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "Hyperliquid",
        "displayName": "Hyperliquid",
        "total24h": 4_000_000,
        "total7d": 28_000_000,
        "total30d": 110_000_000,
        "totalAllTime": 1_200_000_000,
        "change_1d": 3.2,
        "chains": ["Hyperliquid"],
    }
    body.update(overrides)
    return body


def _chain_row(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "gecko_id": "hyperliquid",
        "tvl": 1_500_000_000,
        "tokenSymbol": "HYPE",
        "cmcId": "32196",
        "name": "Hyperliquid",
        "chainId": None,
    }
    body.update(overrides)
    return body


@respx.mock(base_url=_BASE)
async def test_protocol_tvl_maps_hyperliquid(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/protocol/hyperliquid").mock(return_value=httpx.Response(200, json=_protocol()))
    async with llama(runtime) as provider:
        page = await provider.get_protocol_tvl("  Hyperliquid  ", days=30)

    assert page.slug == "hyperliquid"
    assert page.name == "Hyperliquid"
    assert page.symbol == "HYPE"
    assert page.category == "Derivatives"
    assert page.tvl_usd == pytest.approx(1_500_000_000)
    assert page.chain_tvls == (("Hyperliquid", 1_500_000_000.0), ("staking", 200_000_000.0))
    assert page.url == "https://defillama.com/protocol/hyperliquid"
    assert page.provenance.provider == "defillama"
    assert page.provenance.endpoint == "/protocol/hyperliquid"
    # 30 天窗口裁掉 7 月那一点，不把 days 打进 query
    assert len(page.series) == 2
    assert page.series[0].tvl_usd == pytest.approx(1_400_000_000)


@respx.mock(base_url=_BASE)
async def test_protocol_tvl_does_not_put_days_in_query(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/protocol/hyperliquid").mock(
        return_value=httpx.Response(200, json=_protocol())
    )
    async with llama(runtime) as provider:
        await provider.get_protocol_tvl("hyperliquid", days=7)
    assert dict(route.calls[0].request.url.params) == {}


@respx.mock(base_url=_BASE)
async def test_no_auth_header(respx_mock: respx.MockRouter, runtime: ProviderRuntime) -> None:
    route = respx_mock.get("/protocol/hyperliquid").mock(
        return_value=httpx.Response(200, json=_protocol())
    )
    async with llama(runtime) as provider:
        await provider.get_protocol_tvl("hyperliquid")
    assert "authorization" not in route.calls[0].request.headers
    assert "api_key" not in str(route.calls[0].request.url).lower()


@respx.mock(base_url=_BASE)
async def test_chain_tvl_parses_unix_dates(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/v2/historicalChainTvl/Hyperliquid").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"date": _ts(datetime(2026, 7, 1, tzinfo=UTC)), "tvl": 1.0},
                {"date": _ts(datetime(2026, 9, 7, tzinfo=UTC)), "tvl": 1_500_000_000},
            ],
        )
    )
    async with llama(runtime) as provider:
        page = await provider.get_chain_tvl("Hyperliquid", days=30)
    assert page.tvl_usd == pytest.approx(1_500_000_000)
    assert len(page.series) == 1
    assert page.url == "https://defillama.com/chain/Hyperliquid"


@respx.mock(base_url=_BASE)
async def test_fees_and_revenue_are_two_calls(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    fees = respx_mock.get("/summary/fees/hyperliquid").mock(
        side_effect=[
            httpx.Response(200, json=_fees()),
            httpx.Response(
                200,
                json=_fees(total24h=1_000_000, total7d=7_000_000, total30d=30_000_000),
            ),
        ]
    )
    async with llama(runtime) as provider:
        page = await provider.get_fees_revenue("hyperliquid")
    assert fees.call_count == 2
    assert page.fees_24h == pytest.approx(4_000_000)
    assert page.revenue_24h == pytest.approx(1_000_000)
    assert page.revenue_30d == pytest.approx(30_000_000)
    params = [dict(call.request.url.params) for call in fees.calls]
    assert params[0]["dataType"] == "dailyFees"
    assert params[1]["dataType"] == "dailyRevenue"
    assert params[0]["excludeTotalDataChart"] == "true"


@respx.mock(base_url=_BASE)
async def test_missing_revenue_adapter_is_not_fatal(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/summary/fees/hyperliquid").mock(
        side_effect=[
            httpx.Response(200, json=_fees()),
            httpx.Response(400, json={"message": "not found"}),
        ]
    )
    async with llama(runtime) as provider:
        page = await provider.get_fees_revenue("hyperliquid")
    assert page.fees_24h == pytest.approx(4_000_000)
    assert page.revenue_24h is None


@respx.mock(base_url=_BASE)
async def test_dex_volume_maps_totals(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/summary/dexs/hyperliquid").mock(
        return_value=httpx.Response(
            200,
            json=_fees(total24h=9_000_000_000, totalAllTime=200_000_000_000),
        )
    )
    async with llama(runtime) as provider:
        page = await provider.get_dex_volume("hyperliquid")
    assert page.volume_24h == pytest.approx(9_000_000_000)
    assert page.volume_all_time == pytest.approx(200_000_000_000)
    assert page.change_1d == pytest.approx(3.2)
    assert page.chains == ("Hyperliquid",)


@respx.mock(base_url=_BASE)
async def test_chain_overview_matches_case_insensitively(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/v2/chains").mock(
        return_value=httpx.Response(
            200,
            json=[_chain_row(name="Ethereum", tvl=80_000_000_000), _chain_row()],
        )
    )
    async with llama(runtime) as provider:
        page = await provider.get_chain_overview("hyperliquid")
    assert page.name == "Hyperliquid"
    assert page.tvl_usd == pytest.approx(1_500_000_000)
    assert page.token_symbol == _chain_row()["tokenSymbol"]
    assert page.gecko_id == "hyperliquid"
    assert page.url == "https://defillama.com/chain/Hyperliquid"


@respx.mock(base_url=_BASE)
async def test_identical_protocol_queries_hit_the_cache(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/protocol/hyperliquid").mock(
        return_value=httpx.Response(200, json=_protocol())
    )
    async with llama(runtime) as provider:
        first = await provider.get_protocol_tvl("hyperliquid")
        second = await provider.get_protocol_tvl("HYPERLIQUID")
    assert route.call_count == 1
    assert first.provenance.is_cached is False
    assert second.provenance.is_cached is True


@respx.mock(base_url=_BASE, assert_all_called=False)
async def test_empty_slug_does_not_call_upstream(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/protocol/hyperliquid").mock(
        return_value=httpx.Response(200, json=_protocol())
    )
    async with llama(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_protocol_tvl("  ")
    assert exc.value.code is ToolErrorCode.INVALID_INPUT
    assert route.call_count == 0


@respx.mock(base_url=_BASE, assert_all_called=False)
async def test_slash_in_slug_does_not_call_upstream(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={}))
    async with llama(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_protocol_tvl("../secret")
    assert exc.value.code is ToolErrorCode.INVALID_INPUT
    assert route.call_count == 0


@respx.mock(base_url=_BASE, assert_all_called=False)
async def test_days_out_of_range_does_not_call_upstream(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/protocol/hyperliquid").mock(
        return_value=httpx.Response(200, json=_protocol())
    )
    async with llama(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_protocol_tvl("hyperliquid", days=366)
    assert exc.value.code is ToolErrorCode.INVALID_INPUT
    assert route.call_count == 0


@respx.mock(base_url=_BASE)
async def test_http_400_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/protocol/not-a-protocol").mock(
        return_value=httpx.Response(400, json={"message": "Protocol is not in our database"})
    )
    async with llama(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_protocol_tvl("not-a-protocol")
    assert exc.value.code is ToolErrorCode.NOT_FOUND
    assert exc.value.status_code == 400


@respx.mock(base_url=_BASE)
async def test_http_404_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/summary/dexs/not-a-dex").mock(
        return_value=httpx.Response(404, json={"message": "Protocol not found"})
    )
    async with llama(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_dex_volume("not-a-dex")
    assert exc.value.code is ToolErrorCode.NOT_FOUND


@respx.mock(base_url=_BASE)
async def test_empty_chain_history_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/v2/historicalChainTvl/Nope").mock(return_value=httpx.Response(200, json=[]))
    async with llama(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_chain_tvl("Nope")
    assert exc.value.code is ToolErrorCode.NOT_FOUND


@respx.mock(base_url=_BASE)
async def test_unknown_chain_overview_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/v2/chains").mock(return_value=httpx.Response(200, json=[_chain_row()]))
    async with llama(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_chain_overview("ethereum")
    assert exc.value.code is ToolErrorCode.NOT_FOUND


@respx.mock(base_url=_BASE)
async def test_message_only_payload_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/protocol/missing").mock(
        return_value=httpx.Response(200, json={"message": "Protocol is not in our database"})
    )
    async with llama(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_protocol_tvl("missing")
    assert exc.value.code is ToolErrorCode.NOT_FOUND


@respx.mock(base_url=_BASE)
async def test_malformed_json_is_parse_error(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/protocol/hyperliquid").mock(return_value=httpx.Response(200, text="not-json"))
    async with llama(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_protocol_tvl("hyperliquid")
    assert exc.value.code is ToolErrorCode.PARSE_ERROR
