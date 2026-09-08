"""DefiLlama 客户端（P3-2）。

无需 key（§3.6 / §17.2）。限流/缓存/429 走 BaseProvider；本文件只负责
路径、JSON 形状，以及「没有这条协议/链」的错误映射。

四个方法对应 P3-6 的 tool，tool 层不要再解析一遍 JSON：

- `get_protocol_tvl` / `get_chain_tvl` → `get_tvl`
- `get_fees_revenue` → `get_protocol_fees_revenue`（fees 与 revenue 各打一次，
  没有 revenue adapter 时字段为 None，不让整次失败）
- `get_dex_volume` → `get_dex_volume`
- `get_chain_overview` → `get_chain_overview`（先拉 /v2/chains 再按名匹配，
  列表缓存共享）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from agent_service.providers.base import BaseProvider, ProviderResponse
from agent_service.providers.errors import ProviderError
from agent_service.providers.runtime import ProviderRuntime
from agent_service.providers.ttl import CacheTTL
from agent_service.schemas.tools import DataProvenance, ToolErrorCode

_LLAMA_BASE = "https://api.llama.fi"
_MAX_DAYS = 365
_CHAINS_PATH = "/v2/chains"
_EXCLUDE = {"excludeTotalDataChart": True, "excludeTotalDataChartBreakdown": True}


def protocol_page_url(slug: str) -> str:
    """给人点的页面，不是 API URL。"""
    return f"https://defillama.com/protocol/{slug}"


def chain_page_url(chain: str) -> str:
    return f"https://defillama.com/chain/{chain}"


def _protocol_path(slug: str) -> str:
    return f"/protocol/{slug}"


def _chain_tvl_path(chain: str) -> str:
    return f"/v2/historicalChainTvl/{chain}"


def _fees_path(slug: str) -> str:
    return f"/summary/fees/{slug}"


def _dex_path(slug: str) -> str:
    return f"/summary/dexs/{slug}"


@dataclass(frozen=True, slots=True)
class TvlPoint:
    timestamp: datetime
    tvl_usd: float


@dataclass(frozen=True, slots=True)
class ProtocolTvl:
    slug: str
    name: str
    symbol: str | None
    category: str | None
    chains: tuple[str, ...]
    tvl_usd: float | None
    chain_tvls: tuple[tuple[str, float], ...]
    series: tuple[TvlPoint, ...]
    url: str
    provenance: DataProvenance


@dataclass(frozen=True, slots=True)
class ChainTvl:
    chain: str
    tvl_usd: float | None
    series: tuple[TvlPoint, ...]
    url: str
    provenance: DataProvenance


@dataclass(frozen=True, slots=True)
class FeesRevenue:
    slug: str
    name: str | None
    fees_24h: float | None
    fees_7d: float | None
    fees_30d: float | None
    revenue_24h: float | None
    revenue_7d: float | None
    revenue_30d: float | None
    url: str
    provenance: DataProvenance


@dataclass(frozen=True, slots=True)
class DexVolume:
    slug: str
    name: str | None
    volume_24h: float | None
    volume_7d: float | None
    volume_30d: float | None
    volume_all_time: float | None
    change_1d: float | None
    chains: tuple[str, ...]
    url: str
    provenance: DataProvenance


@dataclass(frozen=True, slots=True)
class ChainOverview:
    name: str
    tvl_usd: float | None
    token_symbol: str | None
    gecko_id: str | None
    chain_id: int | None
    url: str
    provenance: DataProvenance


class DefiLlamaProvider(BaseProvider):
    def __init__(
        self,
        *,
        runtime: ProviderRuntime,
        client: httpx.AsyncClient | None = None,
        base_url: str = _LLAMA_BASE,
    ) -> None:
        super().__init__(
            name="defillama",
            base_url=base_url,
            runtime=runtime,
            headers={"Accept": "application/json"},
            client=client,
        )

    async def get_protocol_tvl(self, slug: str, *, days: int = 30) -> ProtocolTvl:
        sid = _require_slug(slug, endpoint="/protocol/{protocol}")
        window = _require_days(days, endpoint=_protocol_path(sid))
        response = await self._get(_protocol_path(sid), CacheTTL.DEFI)
        return _parse_protocol(response, slug=sid, days=window, now=self.runtime.clock.now())

    async def get_chain_tvl(self, chain: str, *, days: int = 30) -> ChainTvl:
        name = _require_slug(chain, endpoint="/v2/historicalChainTvl/{chain}", lower=False)
        window = _require_days(days, endpoint=_chain_tvl_path(name))
        response = await self._get(_chain_tvl_path(name), CacheTTL.DEFI)
        return _parse_chain_tvl(response, chain=name, days=window, now=self.runtime.clock.now())

    async def get_fees_revenue(self, slug: str) -> FeesRevenue:
        sid = _require_slug(slug, endpoint="/summary/fees/{protocol}")
        fees = await self._get(
            _fees_path(sid),
            CacheTTL.DEFI,
            {**_EXCLUDE, "dataType": "dailyFees"},
        )
        parsed_fees = _parse_summary(fees, endpoint=_fees_path(sid))
        revenue_24h = revenue_7d = revenue_30d = None
        try:
            revenue = await self._get(
                _fees_path(sid),
                CacheTTL.DEFI,
                {**_EXCLUDE, "dataType": "dailyRevenue"},
            )
            parsed_rev = _parse_summary(revenue, endpoint=_fees_path(sid))
            revenue_24h, revenue_7d, revenue_30d = (
                parsed_rev.total_24h,
                parsed_rev.total_7d,
                parsed_rev.total_30d,
            )
        except ProviderError as exc:
            if exc.code is not ToolErrorCode.NOT_FOUND:
                raise
        return FeesRevenue(
            slug=sid,
            name=parsed_fees.name,
            fees_24h=parsed_fees.total_24h,
            fees_7d=parsed_fees.total_7d,
            fees_30d=parsed_fees.total_30d,
            revenue_24h=revenue_24h,
            revenue_7d=revenue_7d,
            revenue_30d=revenue_30d,
            url=protocol_page_url(sid),
            provenance=fees.provenance(),
        )

    async def get_dex_volume(self, slug: str) -> DexVolume:
        sid = _require_slug(slug, endpoint="/summary/dexs/{protocol}")
        response = await self._get(_dex_path(sid), CacheTTL.DEFI, dict(_EXCLUDE))
        summary = _parse_summary(response, endpoint=_dex_path(sid))
        return DexVolume(
            slug=sid,
            name=summary.name,
            volume_24h=summary.total_24h,
            volume_7d=summary.total_7d,
            volume_30d=summary.total_30d,
            volume_all_time=summary.total_all_time,
            change_1d=summary.change_1d,
            chains=summary.chains,
            url=protocol_page_url(sid),
            provenance=response.provenance(),
        )

    async def get_chain_overview(self, chain: str) -> ChainOverview:
        name = _require_slug(chain, endpoint=_CHAINS_PATH, lower=False)
        response = await self._get(_CHAINS_PATH, CacheTTL.DEFI)
        return _parse_chain_overview(response, chain=name)

    async def _get(
        self,
        endpoint: str,
        ttl: CacheTTL,
        params: dict[str, str | int | float | bool | None] | None = None,
    ) -> ProviderResponse:
        try:
            return await self.get_json(endpoint, ttl=ttl, params=params)
        except ProviderError as exc:
            raise _map_http_error(exc, endpoint=endpoint) from exc


@dataclass(frozen=True, slots=True)
class _Summary:
    name: str | None
    total_24h: float | None
    total_7d: float | None
    total_30d: float | None
    total_all_time: float | None
    change_1d: float | None
    chains: tuple[str, ...]


def _map_http_error(exc: ProviderError, *, endpoint: str) -> ProviderError:
    if exc.status_code in {httpx.codes.BAD_REQUEST, httpx.codes.NOT_FOUND}:
        return ProviderError(
            ToolErrorCode.NOT_FOUND,
            "DefiLlama 没有这项数据",
            retryable=False,
            status_code=exc.status_code,
            provider="defillama",
            endpoint=endpoint,
        )
    return exc


def _require_slug(value: str, *, endpoint: str, lower: bool = True) -> str:
    slug = value.strip()
    if not slug or "/" in slug or ".." in slug:
        raise ProviderError(
            ToolErrorCode.INVALID_INPUT,
            "slug 为空或含非法字符",
            provider="defillama",
            endpoint=endpoint,
        )
    return slug.lower() if lower else slug


def _require_days(days: int, *, endpoint: str) -> int:
    if days < 1 or days > _MAX_DAYS:
        raise ProviderError(
            ToolErrorCode.INVALID_INPUT,
            f"历史区间必须在 1–{_MAX_DAYS} 天",
            provider="defillama",
            endpoint=endpoint,
        )
    return days


def _parse_protocol(
    response: ProviderResponse, *, slug: str, days: int, now: datetime
) -> ProtocolTvl:
    data = response.data
    if _is_missing_payload(data):
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            f"DefiLlama 没有这个协议：{slug}",
            retryable=False,
            provider=response.provider,
            endpoint=_protocol_path(slug),
        )
    if not isinstance(data, dict):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "DefiLlama 返回了非预期结构（protocol 不是对象）",
            retryable=False,
            provider=response.provider,
            endpoint=_protocol_path(slug),
        )
    name = _optional_str(data.get("name")) or slug
    series = _trim_series(_protocol_series(data.get("tvl")), days, now)
    chain_tvls = _chain_tvl_pairs(data.get("currentChainTvls"))
    current = series[-1].tvl_usd if series else None
    return ProtocolTvl(
        slug=slug,
        name=name,
        symbol=_optional_str(data.get("symbol")),
        category=_optional_str(data.get("category")),
        chains=_string_tuple(data.get("chains")),
        tvl_usd=current,
        chain_tvls=chain_tvls,
        series=series,
        url=protocol_page_url(slug),
        provenance=response.provenance(),
    )


def _parse_chain_tvl(
    response: ProviderResponse, *, chain: str, days: int, now: datetime
) -> ChainTvl:
    data = response.data
    if _is_missing_payload(data):
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            f"DefiLlama 没有这条链：{chain}",
            retryable=False,
            provider=response.provider,
            endpoint=_chain_tvl_path(chain),
        )
    if not isinstance(data, list):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "DefiLlama 返回了非预期结构（chain tvl 不是列表）",
            retryable=False,
            provider=response.provider,
            endpoint=_chain_tvl_path(chain),
        )
    points: list[TvlPoint] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        ts = _from_unix(item.get("date"))
        value = _optional_float(item.get("tvl"))
        if ts is None or value is None:
            continue
        points.append(TvlPoint(timestamp=ts, tvl_usd=value))
    series = _trim_series(tuple(points), days, now)
    if not series:
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            f"DefiLlama 没有这条链：{chain}",
            retryable=False,
            provider=response.provider,
            endpoint=_chain_tvl_path(chain),
        )
    return ChainTvl(
        chain=chain,
        tvl_usd=series[-1].tvl_usd,
        series=series,
        url=chain_page_url(chain),
        provenance=response.provenance(),
    )


def _parse_summary(response: ProviderResponse, *, endpoint: str) -> _Summary:
    data = response.data
    if _is_missing_payload(data):
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            "DefiLlama 没有这项数据",
            retryable=False,
            provider=response.provider,
            endpoint=endpoint,
        )
    if not isinstance(data, dict):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "DefiLlama 返回了非预期结构（summary 不是对象）",
            retryable=False,
            provider=response.provider,
            endpoint=endpoint,
        )
    return _Summary(
        name=_optional_str(data.get("name") or data.get("displayName")),
        total_24h=_optional_float(data.get("total24h")),
        total_7d=_optional_float(data.get("total7d")),
        total_30d=_optional_float(data.get("total30d")),
        total_all_time=_optional_float(data.get("totalAllTime")),
        change_1d=_optional_float(data.get("change_1d")),
        chains=_string_tuple(data.get("chains")),
    )


def _parse_chain_overview(response: ProviderResponse, *, chain: str) -> ChainOverview:
    data = response.data
    if not isinstance(data, list):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "DefiLlama 返回了非预期结构（chains 不是列表）",
            retryable=False,
            provider=response.provider,
            endpoint=_CHAINS_PATH,
        )
    needle = chain.casefold()
    row: dict[str, Any] | None = None
    for item in data:
        if (
            isinstance(item, dict)
            and _optional_str(item.get("name"))
            and str(item["name"]).casefold() == needle
        ):
            row = item
            break
    if row is None:
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            f"DefiLlama 没有这条链：{chain}",
            retryable=False,
            provider=response.provider,
            endpoint=_CHAINS_PATH,
        )
    name = _optional_str(row.get("name")) or chain
    return ChainOverview(
        name=name,
        tvl_usd=_optional_float(row.get("tvl")),
        token_symbol=_optional_str(row.get("tokenSymbol")),
        gecko_id=_optional_str(row.get("gecko_id")),
        chain_id=_optional_int(row.get("chainId")),
        url=chain_page_url(name),
        provenance=response.provenance(),
    )


def _protocol_series(raw: object) -> tuple[TvlPoint, ...]:
    if not isinstance(raw, list):
        return ()
    points: list[TvlPoint] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ts = _from_unix(item.get("date"))
        value = _optional_float(item.get("totalLiquidityUSD"))
        if ts is None or value is None:
            continue
        points.append(TvlPoint(timestamp=ts, tvl_usd=value))
    return tuple(points)


def _chain_tvl_pairs(raw: object) -> tuple[tuple[str, float], ...]:
    if not isinstance(raw, dict):
        return ()
    pairs: list[tuple[str, float]] = []
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        amount = _optional_float(value)
        if amount is None:
            continue
        pairs.append((key, amount))
    return tuple(pairs)


def _trim_series(points: tuple[TvlPoint, ...], days: int, now: datetime) -> tuple[TvlPoint, ...]:
    cutoff = now - timedelta(days=days)
    return tuple(point for point in points if point.timestamp >= cutoff)


def _is_missing_payload(data: object) -> bool:
    if data is None:
        return True
    return (
        isinstance(data, dict)
        and bool(data.get("message"))
        and "name" not in data
        and "tvl" not in data
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    for item in value:
        text = _optional_str(item)
        if text is not None:
            items.append(text)
    return tuple(items)


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


def _from_unix(value: object) -> datetime | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    return None
