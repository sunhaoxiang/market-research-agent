"""crypto 行情 tools（P3-4 验收）。

包 CoinGecko 现成类型，不解析 JSON、不在这里做符号消歧。
asset 必须是 coin_id；provenance.source_url 是给人点的页面。
"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_service.providers.crypto import (
    CoinMarket,
    CoinPrice,
    CoinSearchPage,
    MarketChart,
    PricePoint,
)
from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import DataProvenance, ToolErrorCode
from agent_service.tools.crypto.market import (
    run_get_crypto_price,
    run_get_market_data,
    run_get_price_history,
)
from agent_service.tools.deps import ToolDeps
from agent_service.tools.registry import invoke_tool

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_PAGE = "https://www.coingecko.com/en/coins/hyperliquid"


def _prov(*, endpoint: str) -> DataProvenance:
    return DataProvenance(
        provider="coingecko",
        endpoint=endpoint,
        source_url="https://api.coingecko.com/api/v3" + endpoint,
        retrieved_at=_NOW,
        is_cached=False,
    )


def _quote(
    *,
    market_cap: float | None = 14_000_000_000.0,
    volume_24h: float | None = 200_000_000.0,
    change_24h_pct: float | None = 3.2,
    as_of: datetime | None = _NOW,
) -> CoinPrice:
    return CoinPrice(
        coin_id="hyperliquid",
        vs_currency="usd",
        price=42.5,
        market_cap=market_cap,
        volume_24h=volume_24h,
        change_24h_pct=change_24h_pct,
        as_of=as_of,
        url=_PAGE,
        provenance=_prov(endpoint="/simple/price"),
    )


def _market(*, fdv: float | None = 42_500_000_000.0) -> CoinMarket:
    return CoinMarket(
        coin_id="hyperliquid",
        symbol="HYPE",
        name="Hyperliquid",
        vs_currency="usd",
        current_price=42.5,
        market_cap=14_000_000_000.0,
        fully_diluted_valuation=fdv,
        total_volume=200_000_000.0,
        circulating_supply=333_000_000.0,
        total_supply=1_000_000_000.0,
        max_supply=1_000_000_000.0,
        ath=50.0,
        ath_date=_NOW,
        atl=1.0,
        atl_date=_NOW,
        high_24h=44.0,
        low_24h=40.0,
        change_24h_pct=3.2,
        last_updated=_NOW,
        url=_PAGE,
        provenance=_prov(endpoint="/coins/markets"),
    )


def _chart(*, prices: tuple[PricePoint, ...] | None = None) -> MarketChart:
    series = (
        prices
        if prices is not None
        else (
            PricePoint(timestamp=datetime(2026, 8, 9, tzinfo=UTC), price=40.0),
            PricePoint(timestamp=_NOW, price=42.5),
        )
    )
    return MarketChart(
        coin_id="hyperliquid",
        vs_currency="usd",
        days=30,
        prices=series,
        url=_PAGE,
        provenance=_prov(endpoint="/coins/hyperliquid/market_chart"),
    )


class FakeCoinGecko:
    def __init__(
        self,
        *,
        quote: CoinPrice | None = None,
        market: CoinMarket | None = None,
        chart: MarketChart | None = None,
        error: ProviderError | None = None,
    ) -> None:
        self.quote = quote
        self.market = market
        self.chart = chart
        self.error = error
        self.price_calls: list[tuple[str, str]] = []
        self.market_calls: list[tuple[str, str]] = []
        self.chart_calls: list[tuple[str, int, str]] = []
        self.search_calls: list[str] = []

    async def search_coins(self, query: str) -> CoinSearchPage:
        self.search_calls.append(query)
        raise AssertionError("行情 tools 不应再搜一遍")

    async def get_price(self, coin_id: str, *, vs_currency: str = "usd") -> CoinPrice:
        self.price_calls.append((coin_id, vs_currency))
        if self.error is not None:
            raise self.error
        assert self.quote is not None
        return self.quote

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
        if self.error is not None:
            raise self.error
        assert self.chart is not None
        if days == self.chart.days:
            return self.chart
        return MarketChart(
            coin_id=self.chart.coin_id,
            vs_currency=vs_currency,
            days=days,
            prices=self.chart.prices,
            url=self.chart.url,
            provenance=self.chart.provenance,
        )


async def test_price_maps_quote_and_human_url() -> None:
    gecko = FakeCoinGecko(quote=_quote())
    result = await run_get_crypto_price(ToolDeps(coingecko=gecko), asset="  hyperliquid  ")
    assert gecko.price_calls == [("hyperliquid", "usd")]
    assert gecko.search_calls == []
    assert result.ok is True
    assert result.data is not None
    assert result.data.coin_id == "hyperliquid"
    assert result.data.price == 42.5
    assert result.data.url == _PAGE
    assert result.provenance is not None
    assert result.provenance.source_url == _PAGE
    assert result.provenance.as_of == _NOW
    assert result.quality is None


async def test_price_marks_missing_optional_fields() -> None:
    gecko = FakeCoinGecko(
        quote=_quote(market_cap=None, volume_24h=None, change_24h_pct=None, as_of=None)
    )
    result = await run_get_crypto_price(ToolDeps(coingecko=gecko), asset="hyperliquid")
    assert result.ok is True
    assert result.quality is not None
    assert result.quality.completeness == "partial"
    assert set(result.quality.missing_fields) == {
        "market_cap",
        "volume_24h",
        "change_24h_pct",
        "as_of",
    }


async def test_market_maps_fdv_and_supply() -> None:
    gecko = FakeCoinGecko(market=_market())
    result = await run_get_market_data(ToolDeps(coingecko=gecko), asset="hyperliquid")
    assert gecko.market_calls == [("hyperliquid", "usd")]
    assert result.data is not None
    assert result.data.name == "Hyperliquid"
    assert result.data.fully_diluted_valuation == 42_500_000_000.0
    assert result.data.circulating_supply == 333_000_000.0
    assert result.provenance is not None
    assert result.provenance.source_url == _PAGE


async def test_history_maps_series() -> None:
    gecko = FakeCoinGecko(chart=_chart())
    result = await run_get_price_history(ToolDeps(coingecko=gecko), asset="hyperliquid", days=7)
    assert gecko.chart_calls == [("hyperliquid", 7, "usd")]
    assert result.data is not None
    assert result.data.days == 7
    assert len(result.data.prices) == 2
    assert result.data.prices[-1].price == 42.5
    assert result.provenance is not None
    assert result.provenance.as_of == _NOW


async def test_empty_history_is_not_found() -> None:
    gecko = FakeCoinGecko(chart=_chart(prices=()))
    result = await run_get_price_history(ToolDeps(coingecko=gecko), asset="hyperliquid")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.NOT_FOUND


async def test_blank_asset_does_not_call_provider() -> None:
    gecko = FakeCoinGecko(quote=_quote())
    result = await run_get_crypto_price(ToolDeps(coingecko=gecko), asset="  ")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT
    assert gecko.price_calls == []


async def test_missing_provider_is_unavailable() -> None:
    result = await run_get_market_data(ToolDeps(), asset="hyperliquid")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UPSTREAM_ERROR


async def test_provider_not_found_becomes_tool_result() -> None:
    gecko = FakeCoinGecko(error=ProviderError(ToolErrorCode.NOT_FOUND, "gone", retryable=False))
    result = await run_get_crypto_price(ToolDeps(coingecko=gecko), asset="nope")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.NOT_FOUND


async def test_invoke_price() -> None:
    gecko = FakeCoinGecko(quote=_quote())
    ok = await invoke_tool("get_crypto_price", {"asset": "hyperliquid"}, ToolDeps(coingecko=gecko))
    assert ok.ok is True
    missing = await invoke_tool("get_crypto_price", {}, ToolDeps(coingecko=gecko))
    assert missing.ok is False
    assert missing.error is not None
    assert missing.error.code is ToolErrorCode.INVALID_INPUT
