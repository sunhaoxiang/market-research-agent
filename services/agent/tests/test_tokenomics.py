"""get_tokenomics（P3-7 验收）。

供应量包 CoinGecko `get_market`，不解析 JSON、不做符号消歧。
分配/解锁没有免费 API：空列表进 missing_fields，不要当成 0。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_service.providers.crypto import CoinMarket, CoinPrice, CoinSearchPage, MarketChart
from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import DataProvenance, ToolErrorCode
from agent_service.tools.crypto.bindings import CRYPTO_TOOLS
from agent_service.tools.crypto.tokenomics import _COVERAGE_CAVEAT, run_get_tokenomics
from agent_service.tools.deps import ToolDeps
from agent_service.tools.registry import invoke_tool

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_PAGE = "https://www.coingecko.com/en/coins/hyperliquid"
_HYPE = "HYPE"


def _prov() -> DataProvenance:
    return DataProvenance(
        provider="coingecko",
        endpoint="/coins/markets",
        source_url="https://api.coingecko.com/api/v3/coins/markets",
        retrieved_at=_NOW,
        is_cached=False,
    )


def _market(
    *,
    circulating_supply: float | None = 333_000_000.0,
    total_supply: float | None = 1_000_000_000.0,
    max_supply: float | None = 1_000_000_000.0,
    fdv: float | None = 42_500_000_000.0,
) -> CoinMarket:
    return CoinMarket(
        coin_id="hyperliquid",
        symbol=_HYPE,
        name="Hyperliquid",
        vs_currency="usd",
        current_price=42.5,
        market_cap=14_000_000_000.0,
        fully_diluted_valuation=fdv,
        total_volume=200_000_000.0,
        circulating_supply=circulating_supply,
        total_supply=total_supply,
        max_supply=max_supply,
        ath=50.0,
        ath_date=_NOW,
        atl=1.0,
        atl_date=_NOW,
        high_24h=44.0,
        low_24h=40.0,
        change_24h_pct=3.2,
        last_updated=_NOW,
        url=_PAGE,
        provenance=_prov(),
    )


class FakeCoinGecko:
    def __init__(
        self, *, market: CoinMarket | None = None, error: ProviderError | None = None
    ) -> None:
        self.market = market
        self.error = error
        self.market_calls: list[tuple[str, str]] = []
        self.search_calls: list[str] = []
        self.price_calls: list[tuple[str, str]] = []
        self.chart_calls: list[tuple[str, int, str]] = []

    async def search_coins(self, query: str) -> CoinSearchPage:
        self.search_calls.append(query)
        raise AssertionError("tokenomics 不应再搜一遍")

    async def get_price(self, coin_id: str, *, vs_currency: str = "usd") -> CoinPrice:
        self.price_calls.append((coin_id, vs_currency))
        raise AssertionError("tokenomics 不应再打 get_price")

    async def get_market(self, coin_id: str, *, vs_currency: str = "usd") -> CoinMarket:
        self.market_calls.append((coin_id, vs_currency))
        if self.error is not None:
            raise self.error
        assert self.market is not None
        return self.market

    async def get_market_chart(
        self, coin_id: str, *, days: int = 30, vs_currency: str = "usd"
    ) -> MarketChart:
        self.chart_calls.append((coin_id, days, vs_currency))
        raise AssertionError("tokenomics 不应再打 get_market_chart")


async def test_maps_hype_supply_and_marks_unlocks_missing() -> None:
    gecko = FakeCoinGecko(market=_market())
    result = await run_get_tokenomics(ToolDeps(coingecko=gecko), asset="  hyperliquid  ")
    assert gecko.market_calls == [("hyperliquid", "usd")]
    assert gecko.search_calls == []
    assert gecko.price_calls == []
    assert gecko.chart_calls == []
    assert result.ok is True
    assert result.data is not None
    assert result.data.coin_id == "hyperliquid"
    assert result.data.symbol == _HYPE
    assert result.data.circulating_supply == 333_000_000.0
    assert result.data.total_supply == 1_000_000_000.0
    assert result.data.max_supply == 1_000_000_000.0
    assert result.data.circulating_pct == pytest.approx(0.333)
    assert result.data.allocations == []
    assert result.data.unlocks == []
    assert result.data.url == _PAGE
    assert result.provenance is not None
    assert result.provenance.source_url == _PAGE
    assert result.provenance.as_of == _NOW
    assert result.quality is not None
    assert result.quality.completeness == "partial"
    assert set(result.quality.missing_fields) == {"allocations", "unlocks"}
    assert _COVERAGE_CAVEAT in result.quality.caveats


async def test_missing_max_supply_drops_circulating_pct() -> None:
    gecko = FakeCoinGecko(market=_market(max_supply=None, fdv=None))
    result = await run_get_tokenomics(ToolDeps(coingecko=gecko), asset="hyperliquid")
    assert result.data is not None
    assert result.data.circulating_pct is None
    assert result.quality is not None
    assert set(result.quality.missing_fields) == {
        "max_supply",
        "fully_diluted_valuation",
        "circulating_pct",
        "allocations",
        "unlocks",
    }


async def test_zero_max_supply_is_not_a_ratio() -> None:
    gecko = FakeCoinGecko(market=_market(max_supply=0.0))
    result = await run_get_tokenomics(ToolDeps(coingecko=gecko), asset="hyperliquid")
    assert result.data is not None
    assert result.data.max_supply == 0.0
    assert result.data.circulating_pct is None
    assert result.quality is not None
    assert "circulating_pct" in result.quality.missing_fields


async def test_blank_asset_does_not_call_provider() -> None:
    gecko = FakeCoinGecko(market=_market())
    result = await run_get_tokenomics(ToolDeps(coingecko=gecko), asset="  ")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT
    assert gecko.market_calls == []


async def test_missing_provider_is_unavailable() -> None:
    result = await run_get_tokenomics(ToolDeps(), asset="hyperliquid")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UPSTREAM_ERROR
    assert result.error.provider == "coingecko"


async def test_provider_not_found_becomes_tool_result() -> None:
    gecko = FakeCoinGecko(error=ProviderError(ToolErrorCode.NOT_FOUND, "gone", retryable=False))
    result = await run_get_tokenomics(ToolDeps(coingecko=gecko), asset="nope")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.NOT_FOUND


async def test_invoke_tokenomics() -> None:
    gecko = FakeCoinGecko(market=_market())
    ok = await invoke_tool("get_tokenomics", {"asset": "hyperliquid"}, ToolDeps(coingecko=gecko))
    assert ok.ok is True
    missing = await invoke_tool("get_tokenomics", {}, ToolDeps(coingecko=gecko))
    assert missing.ok is False
    assert missing.error is not None
    assert missing.error.code is ToolErrorCode.INVALID_INPUT


def test_function_tool_includes_tokenomics() -> None:
    assert [tool.name for tool in CRYPTO_TOOLS] == [
        "resolve_asset",
        "get_crypto_price",
        "get_market_data",
        "get_price_history",
        "get_tokenomics",
    ]
