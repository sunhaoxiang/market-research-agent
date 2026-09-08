"""defi tools（P3-6 验收）。

包 DefiLlama 现成类型，不解析 JSON、不另打 HTTP。
HYPE / Hyperliquid 的 slug、链名、页面 URL 必须对上。
"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_service.providers.defi import (
    ChainOverview,
    ChainTvl,
    DexVolume,
    FeesRevenue,
    ProtocolTvl,
    TvlPoint,
)
from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import DataProvenance, ToolErrorCode
from agent_service.tools.defi.bindings import DEFI_TOOLS
from agent_service.tools.defi.llama import (
    run_get_chain_overview,
    run_get_dex_volume,
    run_get_protocol_fees_revenue,
    run_get_tvl,
)
from agent_service.tools.deps import ToolDeps
from agent_service.tools.registry import invoke_tool

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_TVL = 1_500_000_000.0
_PROTO_PAGE = "https://defillama.com/protocol/hyperliquid"
_CHAIN_PAGE = "https://defillama.com/chain/Hyperliquid"
_HYPE = "HYPE"


def _prov(*, endpoint: str) -> DataProvenance:
    return DataProvenance(
        provider="defillama",
        endpoint=endpoint,
        source_url="https://api.llama.fi" + endpoint,
        retrieved_at=_NOW,
        is_cached=False,
    )


def _series(*, empty: bool = False) -> tuple[TvlPoint, ...]:
    if empty:
        return ()
    return (
        TvlPoint(timestamp=datetime(2026, 9, 1, tzinfo=UTC), tvl_usd=1_400_000_000.0),
        TvlPoint(timestamp=_NOW, tvl_usd=_TVL),
    )


def _protocol_tvl(
    *,
    tvl_usd: float | None = _TVL,
    symbol: str | None = _HYPE,
    category: str | None = "Derivatives",
    series: tuple[TvlPoint, ...] | None = None,
) -> ProtocolTvl:
    return ProtocolTvl(
        slug="hyperliquid",
        name="Hyperliquid",
        symbol=symbol,
        category=category,
        chains=("Hyperliquid",),
        tvl_usd=tvl_usd,
        chain_tvls=(("Hyperliquid", _TVL), ("staking", 200_000_000.0)),
        series=_series() if series is None else series,
        url=_PROTO_PAGE,
        provenance=_prov(endpoint="/protocol/hyperliquid"),
    )


def _chain_tvl(
    *, tvl_usd: float | None = _TVL, series: tuple[TvlPoint, ...] | None = None
) -> ChainTvl:
    return ChainTvl(
        chain="Hyperliquid",
        tvl_usd=tvl_usd,
        series=_series() if series is None else series,
        url=_CHAIN_PAGE,
        provenance=_prov(endpoint="/v2/historicalChainTvl/Hyperliquid"),
    )


def _fees(*, revenue: bool = True) -> FeesRevenue:
    return FeesRevenue(
        slug="hyperliquid",
        name="Hyperliquid",
        fees_24h=4_000_000.0,
        fees_7d=28_000_000.0,
        fees_30d=110_000_000.0,
        revenue_24h=2_000_000.0 if revenue else None,
        revenue_7d=14_000_000.0 if revenue else None,
        revenue_30d=55_000_000.0 if revenue else None,
        url=_PROTO_PAGE,
        provenance=_prov(endpoint="/summary/fees/hyperliquid"),
    )


def _dex() -> DexVolume:
    return DexVolume(
        slug="hyperliquid",
        name="Hyperliquid",
        volume_24h=800_000_000.0,
        volume_7d=5_000_000_000.0,
        volume_30d=20_000_000_000.0,
        volume_all_time=200_000_000_000.0,
        change_1d=3.2,
        chains=("Hyperliquid",),
        url=_PROTO_PAGE,
        provenance=_prov(endpoint="/summary/dexs/hyperliquid"),
    )


def _overview() -> ChainOverview:
    return ChainOverview(
        name="Hyperliquid",
        tvl_usd=_TVL,
        token_symbol=_HYPE,
        gecko_id="hyperliquid",
        chain_id=None,
        url=_CHAIN_PAGE,
        provenance=_prov(endpoint="/v2/chains"),
    )


class FakeDefiLlama:
    def __init__(
        self,
        *,
        protocol_tvl: ProtocolTvl | None = None,
        chain_tvl: ChainTvl | None = None,
        fees: FeesRevenue | None = None,
        dex: DexVolume | None = None,
        overview: ChainOverview | None = None,
        error: ProviderError | None = None,
    ) -> None:
        self.protocol_tvl = protocol_tvl
        self.chain_tvl = chain_tvl
        self.fees = fees
        self.dex = dex
        self.overview = overview
        self.error = error
        self.protocol_calls: list[tuple[str, int]] = []
        self.chain_calls: list[tuple[str, int]] = []
        self.fees_calls: list[str] = []
        self.dex_calls: list[str] = []
        self.overview_calls: list[str] = []

    async def get_protocol_tvl(self, slug: str, *, days: int = 30) -> ProtocolTvl:
        self.protocol_calls.append((slug, days))
        if self.error is not None:
            raise self.error
        assert self.protocol_tvl is not None
        return self.protocol_tvl

    async def get_chain_tvl(self, chain: str, *, days: int = 30) -> ChainTvl:
        self.chain_calls.append((chain, days))
        if self.error is not None:
            raise self.error
        assert self.chain_tvl is not None
        return self.chain_tvl

    async def get_fees_revenue(self, slug: str) -> FeesRevenue:
        self.fees_calls.append(slug)
        if self.error is not None:
            raise self.error
        assert self.fees is not None
        return self.fees

    async def get_dex_volume(self, slug: str) -> DexVolume:
        self.dex_calls.append(slug)
        if self.error is not None:
            raise self.error
        assert self.dex is not None
        return self.dex

    async def get_chain_overview(self, chain: str) -> ChainOverview:
        self.overview_calls.append(chain)
        if self.error is not None:
            raise self.error
        assert self.overview is not None
        return self.overview

    async def aclose(self) -> None:
        return None


async def test_protocol_tvl_maps_hyperliquid_and_human_url() -> None:
    llama = FakeDefiLlama(protocol_tvl=_protocol_tvl())
    result = await run_get_tvl(ToolDeps(defillama=llama), protocol="  hyperliquid  ", days=7)
    assert llama.protocol_calls == [("hyperliquid", 7)]
    assert llama.chain_calls == []
    assert result.ok is True
    assert result.data is not None
    assert result.data.scope == "protocol"
    assert result.data.protocol == "hyperliquid"
    assert result.data.name == "Hyperliquid"
    assert result.data.symbol == _HYPE
    assert result.data.tvl_usd == _TVL
    assert result.data.chain_tvls[0].chain == "Hyperliquid"
    assert result.data.series[-1].tvl_usd == _TVL
    assert result.data.url == _PROTO_PAGE
    assert result.provenance is not None
    assert result.provenance.source_url == _PROTO_PAGE
    assert result.provenance.as_of == _NOW
    assert result.quality is None


async def test_chain_tvl_preserves_case() -> None:
    llama = FakeDefiLlama(chain_tvl=_chain_tvl())
    result = await run_get_tvl(ToolDeps(defillama=llama), chain="Hyperliquid")
    assert llama.chain_calls == [("Hyperliquid", 30)]
    assert llama.protocol_calls == []
    assert result.data is not None
    assert result.data.scope == "chain"
    assert result.data.chain == "Hyperliquid"
    assert result.data.tvl_usd == _TVL
    assert result.data.url == _CHAIN_PAGE
    assert result.provenance is not None
    assert result.provenance.source_url == _CHAIN_PAGE
    assert result.quality is None


async def test_tvl_rejects_both_or_neither() -> None:
    llama = FakeDefiLlama(protocol_tvl=_protocol_tvl(), chain_tvl=_chain_tvl())
    both = await run_get_tvl(ToolDeps(defillama=llama), protocol="hyperliquid", chain="Hyperliquid")
    neither = await run_get_tvl(ToolDeps(defillama=llama))
    blank = await run_get_tvl(ToolDeps(defillama=llama), protocol="  ", chain="  ")
    assert both.ok is False
    assert neither.ok is False
    assert blank.ok is False
    assert both.error is not None
    assert both.error.code is ToolErrorCode.INVALID_INPUT
    assert llama.protocol_calls == []
    assert llama.chain_calls == []


async def test_empty_series_is_partial_not_zero() -> None:
    llama = FakeDefiLlama(protocol_tvl=_protocol_tvl(series=_series(empty=True)))
    result = await run_get_tvl(ToolDeps(defillama=llama), protocol="hyperliquid")
    assert result.ok is True
    assert result.data is not None
    assert result.data.series == []
    assert result.data.tvl_usd == _TVL
    assert result.quality is not None
    assert result.quality.completeness == "partial"
    assert "series" in result.quality.missing_fields
    assert result.provenance is not None
    assert result.provenance.as_of is None


async def test_fees_without_revenue_is_partial() -> None:
    llama = FakeDefiLlama(fees=_fees(revenue=False))
    result = await run_get_protocol_fees_revenue(ToolDeps(defillama=llama), protocol="hyperliquid")
    assert llama.fees_calls == ["hyperliquid"]
    assert result.ok is True
    assert result.data is not None
    assert result.data.fees_24h == 4_000_000.0
    assert result.data.revenue_24h is None
    assert result.data.url == _PROTO_PAGE
    assert result.quality is not None
    assert result.quality.completeness == "partial"
    assert set(result.quality.missing_fields) == {
        "revenue_24h",
        "revenue_7d",
        "revenue_30d",
    }


async def test_dex_volume_maps_protocol() -> None:
    llama = FakeDefiLlama(dex=_dex())
    result = await run_get_dex_volume(ToolDeps(defillama=llama), protocol="hyperliquid")
    assert llama.dex_calls == ["hyperliquid"]
    assert result.data is not None
    assert result.data.volume_24h == 800_000_000.0
    assert result.data.chains == ["Hyperliquid"]
    assert result.provenance is not None
    assert result.provenance.source_url == _PROTO_PAGE
    assert result.quality is None


async def test_chain_overview_maps_gecko_id() -> None:
    llama = FakeDefiLlama(overview=_overview())
    result = await run_get_chain_overview(ToolDeps(defillama=llama), chain="hyperliquid")
    assert llama.overview_calls == ["hyperliquid"]
    assert result.data is not None
    assert result.data.name == "Hyperliquid"
    assert result.data.gecko_id == "hyperliquid"
    assert result.data.token_symbol == _HYPE
    assert result.data.tvl_usd == _TVL
    assert result.data.chain_id is None
    assert result.quality is not None
    assert result.quality.missing_fields == ["chain_id"]


async def test_missing_provider_is_unavailable() -> None:
    result = await run_get_tvl(ToolDeps(), protocol="hyperliquid")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UPSTREAM_ERROR
    assert result.error.provider == "defillama"


async def test_provider_not_found_becomes_tool_result() -> None:
    llama = FakeDefiLlama(error=ProviderError(ToolErrorCode.NOT_FOUND, "gone", retryable=False))
    result = await run_get_dex_volume(ToolDeps(defillama=llama), protocol="nope")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.NOT_FOUND


async def test_blank_protocol_does_not_call_provider() -> None:
    llama = FakeDefiLlama(fees=_fees())
    result = await run_get_protocol_fees_revenue(ToolDeps(defillama=llama), protocol="  ")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT
    assert llama.fees_calls == []


async def test_invoke_tvl_and_fees() -> None:
    llama = FakeDefiLlama(protocol_tvl=_protocol_tvl(), fees=_fees())
    deps = ToolDeps(defillama=llama)
    tvl = await invoke_tool("get_tvl", {"protocol": "hyperliquid"}, deps)
    assert tvl.ok is True
    both = await invoke_tool("get_tvl", {"protocol": "hyperliquid", "chain": "Hyperliquid"}, deps)
    assert both.ok is False
    fees = await invoke_tool("get_protocol_fees_revenue", {"protocol": "hyperliquid"}, deps)
    assert fees.ok is True
    missing = await invoke_tool("get_dex_volume", {}, deps)
    assert missing.ok is False
    assert missing.error is not None
    assert missing.error.code is ToolErrorCode.INVALID_INPUT


def test_function_tool_name_is_stable() -> None:
    assert [tool.name for tool in DEFI_TOOLS] == [
        "get_tvl",
        "get_protocol_fees_revenue",
        "get_dex_volume",
        "get_chain_overview",
    ]
