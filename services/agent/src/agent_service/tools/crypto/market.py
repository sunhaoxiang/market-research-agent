"""crypto 行情 tools。包 CoinGecko 现成类型，不再解析 JSON。"""

from __future__ import annotations

from datetime import datetime

from agent_service.providers.crypto import CoinMarket, CoinPrice, MarketChart
from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import (
    DataProvenance,
    DataQuality,
    ToolError,
    ToolErrorCode,
    ToolResult,
)
from agent_service.tools._result import fail_invalid, fail_provider, fail_unavailable
from agent_service.tools.crypto.models import (
    CryptoMarketData,
    CryptoPriceData,
    CryptoPriceHistoryData,
    CryptoPricePoint,
)
from agent_service.tools.deps import ToolDeps

_PRICE = "get_crypto_price"
_MARKET = "get_market_data"
_HISTORY = "get_price_history"
_PRICE_OPTIONAL = ("market_cap", "volume_24h", "change_24h_pct", "as_of")
_MARKET_OPTIONAL = (
    "current_price",
    "market_cap",
    "fully_diluted_valuation",
    "total_volume",
    "circulating_supply",
    "total_supply",
    "max_supply",
    "ath",
    "ath_date",
    "atl",
    "atl_date",
    "high_24h",
    "low_24h",
    "change_24h_pct",
    "last_updated",
)


async def run_get_crypto_price(
    deps: ToolDeps, *, asset: str, vs_currency: str = "usd"
) -> ToolResult[CryptoPriceData]:
    coin_id = asset.strip()
    if not coin_id:
        return fail_invalid(_PRICE, "asset 为空，请先用 resolve_asset 拿到 coin_id")
    if deps.coingecko is None:
        return fail_unavailable(tool=_PRICE, provider="coingecko", message="CoinGecko 未初始化")
    try:
        quote = await deps.coingecko.get_price(coin_id, vs_currency=vs_currency)
    except ProviderError as exc:
        return fail_provider(_PRICE, exc)
    return ToolResult.success(
        _price_data(quote),
        _page_provenance(quote.provenance, quote.url, as_of=quote.as_of),
        quality=_missing_quality(quote, _PRICE_OPTIONAL),
    )


async def run_get_market_data(
    deps: ToolDeps, *, asset: str, vs_currency: str = "usd"
) -> ToolResult[CryptoMarketData]:
    coin_id = asset.strip()
    if not coin_id:
        return fail_invalid(_MARKET, "asset 为空，请先用 resolve_asset 拿到 coin_id")
    if deps.coingecko is None:
        return fail_unavailable(tool=_MARKET, provider="coingecko", message="CoinGecko 未初始化")
    try:
        row = await deps.coingecko.get_market(coin_id, vs_currency=vs_currency)
    except ProviderError as exc:
        return fail_provider(_MARKET, exc)
    return ToolResult.success(
        _market_data(row),
        _page_provenance(row.provenance, row.url, as_of=row.last_updated),
        quality=_missing_quality(row, _MARKET_OPTIONAL),
    )


async def run_get_price_history(
    deps: ToolDeps, *, asset: str, days: int = 30, vs_currency: str = "usd"
) -> ToolResult[CryptoPriceHistoryData]:
    coin_id = asset.strip()
    if not coin_id:
        return fail_invalid(_HISTORY, "asset 为空，请先用 resolve_asset 拿到 coin_id")
    if deps.coingecko is None:
        return fail_unavailable(tool=_HISTORY, provider="coingecko", message="CoinGecko 未初始化")
    try:
        chart = await deps.coingecko.get_market_chart(coin_id, days=days, vs_currency=vs_currency)
    except ProviderError as exc:
        return fail_provider(_HISTORY, exc)
    if not chart.prices:
        return ToolResult.failure(
            ToolError(
                code=ToolErrorCode.NOT_FOUND,
                message=f"没有价格历史：{coin_id}",
                tool=_HISTORY,
                provider="coingecko",
                retryable=False,
            )
        )
    return ToolResult.success(
        _history_data(chart),
        _page_provenance(chart.provenance, chart.url, as_of=chart.prices[-1].timestamp),
    )


def _price_data(quote: CoinPrice) -> CryptoPriceData:
    return CryptoPriceData(
        coin_id=quote.coin_id,
        vs_currency=quote.vs_currency,
        price=quote.price,
        market_cap=quote.market_cap,
        volume_24h=quote.volume_24h,
        change_24h_pct=quote.change_24h_pct,
        as_of=quote.as_of,
        url=quote.url,
    )


def _market_data(row: CoinMarket) -> CryptoMarketData:
    return CryptoMarketData(
        coin_id=row.coin_id,
        symbol=row.symbol,
        name=row.name,
        vs_currency=row.vs_currency,
        current_price=row.current_price,
        market_cap=row.market_cap,
        fully_diluted_valuation=row.fully_diluted_valuation,
        total_volume=row.total_volume,
        circulating_supply=row.circulating_supply,
        total_supply=row.total_supply,
        max_supply=row.max_supply,
        ath=row.ath,
        ath_date=row.ath_date,
        atl=row.atl,
        atl_date=row.atl_date,
        high_24h=row.high_24h,
        low_24h=row.low_24h,
        change_24h_pct=row.change_24h_pct,
        last_updated=row.last_updated,
        url=row.url,
    )


def _history_data(chart: MarketChart) -> CryptoPriceHistoryData:
    return CryptoPriceHistoryData(
        coin_id=chart.coin_id,
        vs_currency=chart.vs_currency,
        days=chart.days,
        prices=[
            CryptoPricePoint(timestamp=point.timestamp, price=point.price) for point in chart.prices
        ],
        url=chart.url,
    )


def _page_provenance(
    provenance: DataProvenance, url: str, *, as_of: datetime | None = None
) -> DataProvenance:
    return provenance.model_copy(update={"source_url": url, "as_of": as_of})


def _missing_quality(row: object, fields: tuple[str, ...]) -> DataQuality | None:
    missing = [name for name in fields if getattr(row, name) is None]
    if not missing:
        return None
    return DataQuality(completeness="partial", missing_fields=missing)
