"""CoinGecko 市场数据客户端（P3-1）。

Demo 档：~30 次/分、1 万次/月（§3.6）。key 可空，走公共限流。
认证用 `x-cg-demo-api-key` 请求头，**不放进 query**——放进 params 会进缓存键，
换 key 写法就让命中率掉到 0，SQLite 里也会留下 secret 的哈希痕迹。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from agent_service.providers.base import BaseProvider, ProviderResponse
from agent_service.providers.errors import ProviderError
from agent_service.providers.runtime import ProviderRuntime
from agent_service.providers.ttl import CacheTTL
from agent_service.schemas.tools import DataProvenance, ToolErrorCode

_CG_BASE = "https://api.coingecko.com/api/v3"
_DEMO_KEY_HEADER = "x-cg-demo-api-key"
_MAX_CHART_DAYS = 365
_SEARCH_PATH = "/search"
_PRICE_PATH = "/simple/price"
_MARKETS_PATH = "/coins/markets"


def coin_page_url(coin_id: str) -> str:
    """给人点的页面，不是 API URL。引用要能打开。"""
    return f"https://www.coingecko.com/en/coins/{coin_id}"


def _chart_path(coin_id: str) -> str:
    return f"/coins/{coin_id}/market_chart"


@dataclass(frozen=True, slots=True)
class CoinSearchHit:
    id: str
    symbol: str
    name: str
    market_cap_rank: int | None
    url: str


@dataclass(frozen=True, slots=True)
class CoinSearchPage:
    query: str
    hits: tuple[CoinSearchHit, ...]
    provenance: DataProvenance


@dataclass(frozen=True, slots=True)
class CoinPrice:
    coin_id: str
    vs_currency: str
    price: float
    market_cap: float | None
    volume_24h: float | None
    change_24h_pct: float | None
    as_of: datetime | None
    url: str
    provenance: DataProvenance


@dataclass(frozen=True, slots=True)
class CoinMarket:
    coin_id: str
    symbol: str
    name: str
    vs_currency: str
    current_price: float | None
    market_cap: float | None
    fully_diluted_valuation: float | None
    total_volume: float | None
    circulating_supply: float | None
    total_supply: float | None
    max_supply: float | None
    ath: float | None
    ath_date: datetime | None
    atl: float | None
    atl_date: datetime | None
    high_24h: float | None
    low_24h: float | None
    change_24h_pct: float | None
    last_updated: datetime | None
    url: str
    provenance: DataProvenance


@dataclass(frozen=True, slots=True)
class PricePoint:
    timestamp: datetime
    price: float


@dataclass(frozen=True, slots=True)
class MarketChart:
    coin_id: str
    vs_currency: str
    days: int
    prices: tuple[PricePoint, ...]
    url: str
    provenance: DataProvenance


class CoinGeckoProvider(BaseProvider):
    def __init__(
        self,
        *,
        runtime: ProviderRuntime,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        base_url: str = _CG_BASE,
    ) -> None:
        key = api_key.strip() if api_key else ""
        headers: dict[str, str] = {"Accept": "application/json"}
        if key:
            headers[_DEMO_KEY_HEADER] = key
        super().__init__(
            name="coingecko",
            base_url=base_url,
            runtime=runtime,
            headers=headers,
            client=client,
        )

    async def search_coins(self, query: str) -> CoinSearchPage:
        q = query.strip()
        if not q:
            raise ProviderError(
                ToolErrorCode.INVALID_INPUT,
                "搜索词为空",
                provider=self.name,
                endpoint=_SEARCH_PATH,
            )
        response = await self._get(_SEARCH_PATH, CacheTTL.MARKET, {"query": q})
        hits = _parse_search(response.data, provider=self.name)
        return CoinSearchPage(query=q, hits=hits, provenance=response.provenance())

    async def get_price(self, coin_id: str, *, vs_currency: str = "usd") -> CoinPrice:
        cid = _require_id(coin_id, endpoint=_PRICE_PATH, provider=self.name)
        vs = _require_vs(vs_currency, endpoint=_PRICE_PATH, provider=self.name)
        response = await self._get(
            _PRICE_PATH,
            CacheTTL.REALTIME,
            {
                "ids": cid,
                "vs_currencies": vs,
                "include_market_cap": True,
                "include_24hr_vol": True,
                "include_24hr_change": True,
                "include_last_updated_at": True,
            },
        )
        return _parse_price(response, coin_id=cid, vs=vs)

    async def get_market(self, coin_id: str, *, vs_currency: str = "usd") -> CoinMarket:
        cid = _require_id(coin_id, endpoint=_MARKETS_PATH, provider=self.name)
        vs = _require_vs(vs_currency, endpoint=_MARKETS_PATH, provider=self.name)
        response = await self._get(
            _MARKETS_PATH,
            CacheTTL.MARKET,
            {"vs_currency": vs, "ids": cid},
        )
        return _parse_market(response, coin_id=cid, vs=vs)

    async def get_market_chart(
        self, coin_id: str, *, days: int = 30, vs_currency: str = "usd"
    ) -> MarketChart:
        cid = _require_id(coin_id, endpoint="/coins/{id}/market_chart", provider=self.name)
        vs = _require_vs(vs_currency, endpoint=_chart_path(cid), provider=self.name)
        if days < 1 or days > _MAX_CHART_DAYS:
            raise ProviderError(
                ToolErrorCode.INVALID_INPUT,
                f"历史区间必须在 1–{_MAX_CHART_DAYS} 天",
                provider=self.name,
                endpoint=_chart_path(cid),
            )
        path = _chart_path(cid)
        response = await self._get(
            path,
            CacheTTL.HISTORY_TODAY,
            {"vs_currency": vs, "days": days},
        )
        return _parse_chart(response, coin_id=cid, vs=vs, days=days)

    async def _get(
        self,
        endpoint: str,
        ttl: CacheTTL,
        params: dict[str, str | int | float | bool | None],
    ) -> ProviderResponse:
        try:
            return await self.get_json(endpoint, ttl=ttl, params=params)
        except ProviderError as exc:
            raise _map_http_error(exc, endpoint=endpoint) from exc


def _map_http_error(exc: ProviderError, *, endpoint: str) -> ProviderError:
    if exc.status_code == httpx.codes.NOT_FOUND:
        return ProviderError(
            ToolErrorCode.NOT_FOUND,
            "CoinGecko 没有这个币",
            retryable=False,
            status_code=exc.status_code,
            provider="coingecko",
            endpoint=endpoint,
        )
    if exc.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
        return ProviderError(
            ToolErrorCode.UPSTREAM_ERROR,
            "CoinGecko API key 无效或未获授权",
            retryable=False,
            status_code=exc.status_code,
            provider="coingecko",
            endpoint=endpoint,
        )
    return exc


def _require_id(coin_id: str, *, endpoint: str, provider: str) -> str:
    cid = coin_id.strip().lower()
    if not cid:
        raise ProviderError(
            ToolErrorCode.INVALID_INPUT,
            "coin_id 为空",
            provider=provider,
            endpoint=endpoint,
        )
    return cid


def _require_vs(vs_currency: str, *, endpoint: str, provider: str) -> str:
    vs = vs_currency.strip().lower()
    if not vs:
        raise ProviderError(
            ToolErrorCode.INVALID_INPUT,
            "计价货币为空",
            provider=provider,
            endpoint=endpoint,
        )
    return vs


def _parse_search(data: Any, *, provider: str) -> tuple[CoinSearchHit, ...]:
    if not isinstance(data, dict) or not isinstance(data.get("coins"), list):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "CoinGecko 返回了非预期结构（缺少 coins）",
            retryable=False,
            provider=provider,
            endpoint=_SEARCH_PATH,
        )
    hits: list[CoinSearchHit] = []
    for item in data["coins"]:
        if not isinstance(item, dict):
            continue
        coin_id = _optional_str(item.get("id"))
        if coin_id is None:
            continue
        hits.append(
            CoinSearchHit(
                id=coin_id,
                symbol=(_optional_str(item.get("symbol")) or coin_id).upper(),
                name=_optional_str(item.get("name")) or coin_id,
                market_cap_rank=_optional_int(item.get("market_cap_rank")),
                url=coin_page_url(coin_id),
            )
        )
    return tuple(hits)


def _parse_price(response: ProviderResponse, *, coin_id: str, vs: str) -> CoinPrice:
    data = response.data
    if not isinstance(data, dict):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "CoinGecko 返回了非预期结构（price 不是对象）",
            retryable=False,
            provider=response.provider,
            endpoint=_PRICE_PATH,
        )
    block = data.get(coin_id)
    if not isinstance(block, dict):
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            f"CoinGecko 没有这个币：{coin_id}",
            retryable=False,
            provider=response.provider,
            endpoint=_PRICE_PATH,
        )
    price = _optional_float(block.get(vs))
    if price is None:
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            f"CoinGecko 未给出 {vs} 价格",
            retryable=False,
            provider=response.provider,
            endpoint=_PRICE_PATH,
        )
    return CoinPrice(
        coin_id=coin_id,
        vs_currency=vs,
        price=price,
        market_cap=_optional_float(block.get(f"{vs}_market_cap")),
        volume_24h=_optional_float(block.get(f"{vs}_24h_vol")),
        change_24h_pct=_optional_float(block.get(f"{vs}_24h_change")),
        as_of=_from_unix(block.get("last_updated_at")),
        url=coin_page_url(coin_id),
        provenance=response.provenance(),
    )


def _parse_market(response: ProviderResponse, *, coin_id: str, vs: str) -> CoinMarket:
    data = response.data
    if not isinstance(data, list):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "CoinGecko 返回了非预期结构（markets 不是列表）",
            retryable=False,
            provider=response.provider,
            endpoint=_MARKETS_PATH,
        )
    row: dict[str, Any] | None = None
    for item in data:
        if isinstance(item, dict) and item.get("id") == coin_id:
            row = item
            break
    if row is None:
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            f"CoinGecko 没有这个币：{coin_id}",
            retryable=False,
            provider=response.provider,
            endpoint=_MARKETS_PATH,
        )
    return CoinMarket(
        coin_id=coin_id,
        symbol=(_optional_str(row.get("symbol")) or coin_id).upper(),
        name=_optional_str(row.get("name")) or coin_id,
        vs_currency=vs,
        current_price=_optional_float(row.get("current_price")),
        market_cap=_optional_float(row.get("market_cap")),
        fully_diluted_valuation=_optional_float(row.get("fully_diluted_valuation")),
        total_volume=_optional_float(row.get("total_volume")),
        circulating_supply=_optional_float(row.get("circulating_supply")),
        total_supply=_optional_float(row.get("total_supply")),
        max_supply=_optional_float(row.get("max_supply")),
        ath=_optional_float(row.get("ath")),
        ath_date=_optional_datetime(row.get("ath_date")),
        atl=_optional_float(row.get("atl")),
        atl_date=_optional_datetime(row.get("atl_date")),
        high_24h=_optional_float(row.get("high_24h")),
        low_24h=_optional_float(row.get("low_24h")),
        change_24h_pct=_optional_float(row.get("price_change_percentage_24h")),
        last_updated=_optional_datetime(row.get("last_updated")),
        url=coin_page_url(coin_id),
        provenance=response.provenance(),
    )


def _parse_chart(response: ProviderResponse, *, coin_id: str, vs: str, days: int) -> MarketChart:
    data = response.data
    if not isinstance(data, dict) or not isinstance(data.get("prices"), list):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "CoinGecko 返回了非预期结构（缺少 prices）",
            retryable=False,
            provider=response.provider,
            endpoint=_chart_path(coin_id),
        )
    points: list[PricePoint] = []
    for item in data["prices"]:
        match item:
            case [raw_ts, raw_price, *_]:
                ts = _from_millis(raw_ts)
                price = _optional_float(raw_price)
            case _:
                continue
        if ts is None or price is None:
            continue
        points.append(PricePoint(timestamp=ts, price=price))
    return MarketChart(
        coin_id=coin_id,
        vs_currency=vs,
        days=days,
        prices=tuple(points),
        url=coin_page_url(coin_id),
        provenance=response.provenance(),
    )


def _optional_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _from_unix(value: object) -> datetime | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    return None


def _from_millis(value: object) -> datetime | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    return None
