"""resolve_asset（P3-3 验收）。

底层只调 `search_coins`，不包 HTTP。钉死："HYPE" 解析到 Hyperliquid；
同符号多候选时有唯一最优市值排名则自动选取，并列则返回列表让 Agent 选。
"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_service.providers.crypto import (
    CoinMarket,
    CoinPrice,
    CoinSearchHit,
    CoinSearchPage,
    MarketChart,
)
from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import DataProvenance, ToolErrorCode
from agent_service.tools.crypto.bindings import CRYPTO_TOOLS
from agent_service.tools.crypto.models import AssetMatchKind
from agent_service.tools.crypto.resolve import disambiguate_coins, run_resolve_asset
from agent_service.tools.deps import ToolDeps
from agent_service.tools.registry import invoke_tool

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _prov(**overrides: object) -> DataProvenance:
    body: dict[str, object] = {
        "provider": "coingecko",
        "endpoint": "/search",
        "retrieved_at": _NOW,
        "is_cached": False,
    }
    body.update(overrides)
    return DataProvenance.model_validate(body)


def _hit(
    *,
    coin_id: str = "hyperliquid",
    symbol: str = "HYPE",
    name: str = "Hyperliquid",
    market_cap_rank: int | None = 15,
) -> CoinSearchHit:
    return CoinSearchHit(
        id=coin_id,
        symbol=symbol,
        name=name,
        market_cap_rank=market_cap_rank,
        url=f"https://www.coingecko.com/en/coins/{coin_id}",
    )


def _page(*hits: CoinSearchHit, query: str = "HYPE") -> CoinSearchPage:
    return CoinSearchPage(query=query, hits=hits, provenance=_prov())


class FakeCoinGecko:
    def __init__(
        self, page: CoinSearchPage | None = None, error: ProviderError | None = None
    ) -> None:
        self.page = page
        self.error = error
        self.queries: list[str] = []

    async def search_coins(self, query: str) -> CoinSearchPage:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        assert self.page is not None
        return self.page

    async def get_price(self, coin_id: str, *, vs_currency: str = "usd") -> CoinPrice:
        raise AssertionError("search-only fake")

    async def get_market(self, coin_id: str, *, vs_currency: str = "usd") -> CoinMarket:
        raise AssertionError("search-only fake")

    async def get_market_chart(
        self, coin_id: str, *, days: int = 30, vs_currency: str = "usd"
    ) -> MarketChart:
        raise AssertionError("search-only fake")

    async def aclose(self) -> None:
        return None


def test_hype_resolves_to_hyperliquid() -> None:
    decision = disambiguate_coins("HYPE", (_hit(),))
    assert decision.kind is AssetMatchKind.EXACT_SYMBOL
    assert decision.resolved is not None
    assert decision.resolved.id == "hyperliquid"


async def test_hype_picks_ranked_symbol_among_collisions() -> None:
    gecko = FakeCoinGecko(
        _page(
            _hit(coin_id="hype-meme", name="Hype", market_cap_rank=None),
            _hit(),
            _hit(coin_id="hype-old", name="HYPE Token", market_cap_rank=2400),
        )
    )
    result = await run_resolve_asset(ToolDeps(coingecko=gecko), query="  HYPE  ")
    assert gecko.queries == ["HYPE"]
    assert result.ok is True
    assert result.data is not None
    assert result.data.match is AssetMatchKind.RANKED_SYMBOL
    assert result.data.resolved is not None
    assert result.data.resolved.coin_id == "hyperliquid"
    assert result.data.resolved.name == "Hyperliquid"
    assert result.data.candidates[0].coin_id == "hyperliquid"
    assert {item.coin_id for item in result.data.candidates} >= {
        "hyperliquid",
        "hype-old",
        "hype-meme",
    }
    assert result.provenance is not None
    assert result.provenance.source_url == "https://www.coingecko.com/en/coins/hyperliquid"
    assert result.quality is not None
    assert result.quality.caveats


async def test_tied_ranks_return_candidates_without_picking() -> None:
    gecko = FakeCoinGecko(
        _page(
            _hit(coin_id="alpha-hype", name="Alpha HYPE", market_cap_rank=100),
            _hit(coin_id="beta-hype", name="Beta HYPE", market_cap_rank=100),
        )
    )
    result = await run_resolve_asset(ToolDeps(coingecko=gecko), query="HYPE")
    assert result.ok is True
    assert result.data is not None
    assert result.data.match is AssetMatchKind.AMBIGUOUS
    assert result.data.resolved is None
    assert {item.coin_id for item in result.data.candidates} == {"alpha-hype", "beta-hype"}
    assert result.quality is not None
    assert result.quality.completeness == "partial"
    assert "resolved" in result.quality.missing_fields


async def test_exact_id_wins_over_symbol() -> None:
    gecko = FakeCoinGecko(
        _page(
            _hit(),
            _hit(coin_id="wrapped-hype", symbol="WHYPE", name="Wrapped HYPE"),
            query="hyperliquid",
        )
    )
    result = await run_resolve_asset(ToolDeps(coingecko=gecko), query="hyperliquid")
    assert result.data is not None
    assert result.data.match is AssetMatchKind.EXACT_ID
    assert result.data.resolved is not None
    assert result.data.resolved.coin_id == "hyperliquid"


async def test_exact_name_resolves() -> None:
    gecko = FakeCoinGecko(
        _page(
            _hit(coin_id="avalanche-2", symbol="AVAX", name="Avalanche"),
            query="Avalanche",
        )
    )
    result = await run_resolve_asset(ToolDeps(coingecko=gecko), query="Avalanche")
    assert result.data is not None
    assert result.data.match is AssetMatchKind.EXACT_NAME
    assert result.data.resolved is not None
    assert result.data.resolved.coin_id == "avalanche-2"


async def test_single_fuzzy_hit_is_unique() -> None:
    gecko = FakeCoinGecko(_page(_hit(), query="hyper liq"))
    result = await run_resolve_asset(ToolDeps(coingecko=gecko), query="hyper liq")
    assert result.data is not None
    assert result.data.match is AssetMatchKind.UNIQUE_HIT
    assert result.data.resolved is not None
    assert result.data.resolved.coin_id == "hyperliquid"


async def test_empty_hits_are_not_found() -> None:
    gecko = FakeCoinGecko(_page(query="zzzz"))
    result = await run_resolve_asset(ToolDeps(coingecko=gecko), query="zzzz")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.NOT_FOUND


async def test_blank_query_does_not_call_provider() -> None:
    gecko = FakeCoinGecko(_page())
    result = await run_resolve_asset(ToolDeps(coingecko=gecko), query="  ")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT
    assert gecko.queries == []


async def test_missing_provider_is_unavailable() -> None:
    result = await run_resolve_asset(ToolDeps(), query="HYPE")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UPSTREAM_ERROR
    assert result.error.provider == "coingecko"


async def test_provider_error_becomes_tool_result() -> None:
    gecko = FakeCoinGecko(
        error=ProviderError(ToolErrorCode.RATE_LIMITED, "slow down", retryable=True)
    )
    result = await run_resolve_asset(ToolDeps(coingecko=gecko), query="HYPE")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.RATE_LIMITED


async def test_invoke_registry() -> None:
    gecko = FakeCoinGecko(_page(_hit()))
    ok = await invoke_tool("resolve_asset", {"query": "HYPE"}, ToolDeps(coingecko=gecko))
    assert ok.ok is True
    missing = await invoke_tool("resolve_asset", {}, ToolDeps(coingecko=gecko))
    assert missing.ok is False
    assert missing.error is not None
    assert missing.error.code is ToolErrorCode.INVALID_INPUT


def test_function_tool_name_is_stable() -> None:
    assert [tool.name for tool in CRYPTO_TOOLS] == [
        "resolve_asset",
        "get_crypto_price",
        "get_market_data",
        "get_price_history",
    ]
