"""defi tools。包 DefiLlama 现成类型，不再解析 JSON。"""

from __future__ import annotations

from datetime import datetime

from agent_service.providers.defi import ChainTvl, ProtocolTvl, TvlPoint
from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import DataProvenance, DataQuality, ToolResult
from agent_service.tools._result import fail_invalid, fail_provider, fail_unavailable
from agent_service.tools.defi.models import (
    ChainOverviewData,
    ChainTvlShare,
    DexVolumeData,
    FeesRevenueData,
    TvlData,
    TvlPointData,
)
from agent_service.tools.deps import DefiLlamaClient, ToolDeps

_TVL = "get_tvl"
_FEES = "get_protocol_fees_revenue"
_DEX = "get_dex_volume"
_OVERVIEW = "get_chain_overview"
_PROTOCOL_TVL_OPTIONAL = ("tvl_usd", "symbol", "category")
_CHAIN_TVL_OPTIONAL = ("tvl_usd",)
_FEES_OPTIONAL = (
    "name",
    "fees_24h",
    "fees_7d",
    "fees_30d",
    "revenue_24h",
    "revenue_7d",
    "revenue_30d",
)
_DEX_OPTIONAL = (
    "name",
    "volume_24h",
    "volume_7d",
    "volume_30d",
    "volume_all_time",
    "change_1d",
)
_OVERVIEW_OPTIONAL = ("tvl_usd", "token_symbol", "gecko_id", "chain_id")


async def run_get_tvl(
    deps: ToolDeps,
    *,
    protocol: str | None = None,
    chain: str | None = None,
    days: int = 30,
) -> ToolResult[TvlData]:
    proto = _blank_to_none(protocol)
    ch = _blank_to_none(chain)
    if proto is not None and ch is not None:
        return fail_invalid(_TVL, "只填 protocol 或 chain 其中一个")
    if proto is None and ch is None:
        return fail_invalid(_TVL, "需要 protocol 或 chain")
    if deps.defillama is None:
        return fail_unavailable(tool=_TVL, provider="defillama", message="DefiLlama 未初始化")
    try:
        return (
            await _protocol_tvl_result(deps.defillama, proto, days)
            if proto is not None
            else await _chain_tvl_result(deps.defillama, ch or "", days)
        )
    except ProviderError as exc:
        return fail_provider(_TVL, exc)


async def _protocol_tvl_result(
    client: DefiLlamaClient, slug: str, days: int
) -> ToolResult[TvlData]:
    page = await client.get_protocol_tvl(slug, days=days)
    data = _protocol_tvl(page)
    extra = () if page.series else ("series",)
    return ToolResult.success(
        data,
        _page_provenance(page.provenance, page.url, as_of=_series_as_of(page.series)),
        quality=_missing_quality(data, _PROTOCOL_TVL_OPTIONAL, extra=extra),
    )


async def _chain_tvl_result(client: DefiLlamaClient, chain: str, days: int) -> ToolResult[TvlData]:
    page = await client.get_chain_tvl(chain, days=days)
    data = _chain_tvl(page)
    extra = () if page.series else ("series",)
    return ToolResult.success(
        data,
        _page_provenance(page.provenance, page.url, as_of=_series_as_of(page.series)),
        quality=_missing_quality(data, _CHAIN_TVL_OPTIONAL, extra=extra),
    )


async def run_get_protocol_fees_revenue(
    deps: ToolDeps, *, protocol: str
) -> ToolResult[FeesRevenueData]:
    slug = protocol.strip()
    if not slug:
        return fail_invalid(_FEES, "protocol 为空")
    if deps.defillama is None:
        return fail_unavailable(tool=_FEES, provider="defillama", message="DefiLlama 未初始化")
    try:
        page = await deps.defillama.get_fees_revenue(slug)
    except ProviderError as exc:
        return fail_provider(_FEES, exc)
    data = FeesRevenueData(
        protocol=page.slug,
        name=page.name,
        fees_24h=page.fees_24h,
        fees_7d=page.fees_7d,
        fees_30d=page.fees_30d,
        revenue_24h=page.revenue_24h,
        revenue_7d=page.revenue_7d,
        revenue_30d=page.revenue_30d,
        url=page.url,
    )
    return ToolResult.success(
        data,
        _page_provenance(page.provenance, page.url),
        quality=_missing_quality(data, _FEES_OPTIONAL),
    )


async def run_get_dex_volume(deps: ToolDeps, *, protocol: str) -> ToolResult[DexVolumeData]:
    slug = protocol.strip()
    if not slug:
        return fail_invalid(_DEX, "protocol 为空")
    if deps.defillama is None:
        return fail_unavailable(tool=_DEX, provider="defillama", message="DefiLlama 未初始化")
    try:
        page = await deps.defillama.get_dex_volume(slug)
    except ProviderError as exc:
        return fail_provider(_DEX, exc)
    data = DexVolumeData(
        protocol=page.slug,
        name=page.name,
        volume_24h=page.volume_24h,
        volume_7d=page.volume_7d,
        volume_30d=page.volume_30d,
        volume_all_time=page.volume_all_time,
        change_1d=page.change_1d,
        chains=list(page.chains),
        url=page.url,
    )
    return ToolResult.success(
        data,
        _page_provenance(page.provenance, page.url),
        quality=_missing_quality(data, _DEX_OPTIONAL),
    )


async def run_get_chain_overview(deps: ToolDeps, *, chain: str) -> ToolResult[ChainOverviewData]:
    name = chain.strip()
    if not name:
        return fail_invalid(_OVERVIEW, "chain 为空")
    if deps.defillama is None:
        return fail_unavailable(tool=_OVERVIEW, provider="defillama", message="DefiLlama 未初始化")
    try:
        page = await deps.defillama.get_chain_overview(name)
    except ProviderError as exc:
        return fail_provider(_OVERVIEW, exc)
    data = ChainOverviewData(
        name=page.name,
        tvl_usd=page.tvl_usd,
        token_symbol=page.token_symbol,
        gecko_id=page.gecko_id,
        chain_id=page.chain_id,
        url=page.url,
    )
    return ToolResult.success(
        data,
        _page_provenance(page.provenance, page.url),
        quality=_missing_quality(data, _OVERVIEW_OPTIONAL),
    )


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _protocol_tvl(page: ProtocolTvl) -> TvlData:
    return TvlData(
        scope="protocol",
        protocol=page.slug,
        name=page.name,
        symbol=page.symbol,
        category=page.category,
        chains=list(page.chains),
        tvl_usd=page.tvl_usd,
        chain_tvls=[ChainTvlShare(chain=name, tvl_usd=amount) for name, amount in page.chain_tvls],
        series=_series(page.series),
        url=page.url,
    )


def _chain_tvl(page: ChainTvl) -> TvlData:
    return TvlData(
        scope="chain",
        chain=page.chain,
        name=page.chain,
        tvl_usd=page.tvl_usd,
        series=_series(page.series),
        url=page.url,
    )


def _series(points: tuple[TvlPoint, ...]) -> list[TvlPointData]:
    return [TvlPointData(timestamp=point.timestamp, tvl_usd=point.tvl_usd) for point in points]


def _series_as_of(points: tuple[TvlPoint, ...]) -> datetime | None:
    if not points:
        return None
    return points[-1].timestamp


def _page_provenance(
    provenance: DataProvenance, url: str, *, as_of: datetime | None = None
) -> DataProvenance:
    return provenance.model_copy(update={"source_url": url, "as_of": as_of})


def _missing_quality(
    row: object, fields: tuple[str, ...], *, extra: tuple[str, ...] = ()
) -> DataQuality | None:
    missing = [name for name in fields if getattr(row, name) is None]
    missing.extend(name for name in extra if name not in missing)
    if not missing:
        return None
    return DataQuality(completeness="partial", missing_fields=missing)
