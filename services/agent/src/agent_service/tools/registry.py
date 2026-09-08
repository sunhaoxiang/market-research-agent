"""按名字调度 tool。HTTP invoke 与 eval 都走这里，不经过 LLM。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from agent_service.schemas.tools import ToolResult
from agent_service.tools._result import fail_validation
from agent_service.tools.crypto.market import (
    run_get_crypto_price,
    run_get_market_data,
    run_get_price_history,
)
from agent_service.tools.crypto.models import (
    CryptoMarketData,
    CryptoPriceData,
    CryptoPriceHistoryData,
    ResolveAssetData,
)
from agent_service.tools.crypto.resolve import run_resolve_asset
from agent_service.tools.defi.llama import (
    run_get_chain_overview,
    run_get_dex_volume,
    run_get_protocol_fees_revenue,
    run_get_tvl,
)
from agent_service.tools.defi.models import (
    ChainOverviewData,
    DexVolumeData,
    FeesRevenueData,
    TvlData,
)
from agent_service.tools.deps import ToolDeps
from agent_service.tools.system.compute import run_compute_metrics
from agent_service.tools.system.models import ComputeMetricsData, SeriesPoint
from agent_service.tools.web.fetch import run_web_fetch
from agent_service.tools.web.models import WebPageData, WebSearchData
from agent_service.tools.web.search import run_news_search, run_web_search

type ToolHandler = Callable[[ToolDeps, dict[str, Any]], Awaitable[ToolResult[Any]]]


class WebSearchArgs(BaseModel):
    query: str
    max_results: int = 5
    time_range: str | None = None
    include_domains: list[str] = Field(default_factory=list)


class NewsSearchArgs(BaseModel):
    query: str
    symbols: list[str] = Field(default_factory=list)
    since: str | None = None


class WebFetchArgs(BaseModel):
    url: str


class ResolveAssetArgs(BaseModel):
    query: str


class CryptoPriceArgs(BaseModel):
    asset: str
    vs_currency: str = "usd"


class CryptoMarketArgs(BaseModel):
    asset: str
    vs_currency: str = "usd"


class CryptoHistoryArgs(BaseModel):
    asset: str
    days: int = 30
    vs_currency: str = "usd"


class ComputeMetricsArgs(BaseModel):
    series: list[SeriesPoint]
    ops: list[str]
    years: float | None = None
    periods_per_year: float | None = None


class GetTvlArgs(BaseModel):
    protocol: str | None = None
    chain: str | None = None
    days: int = 30


class ProtocolSlugArgs(BaseModel):
    protocol: str


class ChainNameArgs(BaseModel):
    chain: str


async def _web_search(deps: ToolDeps, arguments: dict[str, Any]) -> ToolResult[WebSearchData]:
    try:
        args = WebSearchArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("web_search", exc)
    return await run_web_search(
        deps,
        query=args.query,
        max_results=args.max_results,
        time_range=args.time_range,
        include_domains=args.include_domains,
    )


async def _news_search(deps: ToolDeps, arguments: dict[str, Any]) -> ToolResult[WebSearchData]:
    try:
        args = NewsSearchArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("news_search", exc)
    return await run_news_search(deps, query=args.query, symbols=args.symbols, since=args.since)


async def _web_fetch(deps: ToolDeps, arguments: dict[str, Any]) -> ToolResult[WebPageData]:
    try:
        args = WebFetchArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("web_fetch", exc)
    return await run_web_fetch(deps, url=args.url)


async def _resolve_asset(deps: ToolDeps, arguments: dict[str, Any]) -> ToolResult[ResolveAssetData]:
    try:
        args = ResolveAssetArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("resolve_asset", exc)
    return await run_resolve_asset(deps, query=args.query)


async def _get_crypto_price(
    deps: ToolDeps, arguments: dict[str, Any]
) -> ToolResult[CryptoPriceData]:
    try:
        args = CryptoPriceArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("get_crypto_price", exc)
    return await run_get_crypto_price(deps, asset=args.asset, vs_currency=args.vs_currency)


async def _get_market_data(
    deps: ToolDeps, arguments: dict[str, Any]
) -> ToolResult[CryptoMarketData]:
    try:
        args = CryptoMarketArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("get_market_data", exc)
    return await run_get_market_data(deps, asset=args.asset, vs_currency=args.vs_currency)


async def _get_price_history(
    deps: ToolDeps, arguments: dict[str, Any]
) -> ToolResult[CryptoPriceHistoryData]:
    try:
        args = CryptoHistoryArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("get_price_history", exc)
    return await run_get_price_history(
        deps, asset=args.asset, days=args.days, vs_currency=args.vs_currency
    )


async def _compute_metrics(
    deps: ToolDeps, arguments: dict[str, Any]
) -> ToolResult[ComputeMetricsData]:
    try:
        args = ComputeMetricsArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("compute_metrics", exc)
    return run_compute_metrics(
        deps,
        series=args.series,
        ops=args.ops,
        years=args.years,
        periods_per_year=args.periods_per_year,
    )


async def _get_tvl(deps: ToolDeps, arguments: dict[str, Any]) -> ToolResult[TvlData]:
    try:
        args = GetTvlArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("get_tvl", exc)
    return await run_get_tvl(deps, protocol=args.protocol, chain=args.chain, days=args.days)


async def _get_protocol_fees_revenue(
    deps: ToolDeps, arguments: dict[str, Any]
) -> ToolResult[FeesRevenueData]:
    try:
        args = ProtocolSlugArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("get_protocol_fees_revenue", exc)
    return await run_get_protocol_fees_revenue(deps, protocol=args.protocol)


async def _get_dex_volume(deps: ToolDeps, arguments: dict[str, Any]) -> ToolResult[DexVolumeData]:
    try:
        args = ProtocolSlugArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("get_dex_volume", exc)
    return await run_get_dex_volume(deps, protocol=args.protocol)


async def _get_chain_overview(
    deps: ToolDeps, arguments: dict[str, Any]
) -> ToolResult[ChainOverviewData]:
    try:
        args = ChainNameArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("get_chain_overview", exc)
    return await run_get_chain_overview(deps, chain=args.chain)


HANDLERS: dict[str, ToolHandler] = {
    "compute_metrics": _compute_metrics,
    "get_chain_overview": _get_chain_overview,
    "get_crypto_price": _get_crypto_price,
    "get_dex_volume": _get_dex_volume,
    "get_market_data": _get_market_data,
    "get_price_history": _get_price_history,
    "get_protocol_fees_revenue": _get_protocol_fees_revenue,
    "get_tvl": _get_tvl,
    "news_search": _news_search,
    "resolve_asset": _resolve_asset,
    "web_fetch": _web_fetch,
    "web_search": _web_search,
}


async def invoke_tool(name: str, arguments: dict[str, Any], deps: ToolDeps) -> ToolResult[Any]:
    handler = HANDLERS[name]
    return await handler(deps, arguments)
