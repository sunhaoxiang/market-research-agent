"""Tool 运行时依赖。

Provider 由 lifespan 注入，再经 `RunContextWrapper.context` 传给
`@function_tool`。HTTP `/v1/tools/{name}/invoke` 走同一份 `ToolDeps`，
这样调试路径和 Agent 路径不会出现「一边有 key、一边没有」的分叉。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent_service.observability.event_bus import EventBus
    from agent_service.providers.crypto import CoinMarket, CoinPrice, CoinSearchPage, MarketChart
    from agent_service.providers.defi import (
        ChainOverview,
        ChainTvl,
        DexVolume,
        FeesRevenue,
        ProtocolTvl,
    )
    from agent_service.providers.equity import (
        BalanceSheets,
        CashFlowStatements,
        IncomeStatements,
        StockHistory,
        StockPeers,
        StockProfile,
        StockQuote,
        ValuationRatioHistory,
        ValuationRatios,
    )
    from agent_service.providers.fetch import PageFetcher
    from agent_service.providers.onchain import PerpMarketSnapshot
    from agent_service.providers.runtime import Clock
    from agent_service.providers.search import SearchProvider
    from agent_service.providers.sec import CompanyFacts, TickerDirectory
    from agent_service.tools.web.collector import SourceCollector


class CoinGeckoClient(Protocol):
    """CoinGecko 在 ToolDeps 上的面。P3-3 用 search；P3-4 用行情方法。假客户端按需实现。"""

    async def search_coins(self, query: str) -> CoinSearchPage: ...

    async def get_price(self, coin_id: str, *, vs_currency: str = "usd") -> CoinPrice: ...

    async def get_market(self, coin_id: str, *, vs_currency: str = "usd") -> CoinMarket: ...

    async def get_market_chart(
        self, coin_id: str, *, days: int = 30, vs_currency: str = "usd"
    ) -> MarketChart: ...


class DefiLlamaClient(Protocol):
    """DefiLlama 在 ToolDeps 上的面。P3-6 四个 tool 只调这些方法。"""

    async def get_protocol_tvl(self, slug: str, *, days: int = 30) -> ProtocolTvl: ...

    async def get_chain_tvl(self, chain: str, *, days: int = 30) -> ChainTvl: ...

    async def get_fees_revenue(self, slug: str) -> FeesRevenue: ...

    async def get_dex_volume(self, slug: str) -> DexVolume: ...

    async def get_chain_overview(self, chain: str) -> ChainOverview: ...


class HyperliquidClient(Protocol):
    """Hyperliquid 在 ToolDeps 上的面。P3-8 只调永续快照。"""

    async def get_perp_snapshot(self) -> PerpMarketSnapshot: ...


class SecEdgarClient(Protocol):
    """SEC 在 ToolDeps 上的面。P4-3 用 ticker 目录；P4-5 用 companyfacts。"""

    async def get_ticker_directory(self) -> TickerDirectory: ...

    async def get_company_facts(self, cik: str) -> CompanyFacts: ...


class FmpClient(Protocol):
    """FMP 在 ToolDeps 上的面。P4-4 行情；P4-5 三表兜底；P4-7 估值。"""

    async def get_quote(self, symbol: str) -> StockQuote: ...

    async def get_profile(self, symbol: str) -> StockProfile: ...

    async def get_historical_prices(self, symbol: str, *, days: int = 30) -> StockHistory: ...

    async def get_peers(self, symbol: str) -> StockPeers: ...

    async def get_income_statements(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> IncomeStatements: ...

    async def get_balance_sheets(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> BalanceSheets: ...

    async def get_cash_flow_statements(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> CashFlowStatements: ...

    async def get_ratios_ttm(self, symbol: str) -> ValuationRatios: ...

    async def get_ratios(
        self, symbol: str, *, period: str = "quarterly", limit: int = 20
    ) -> ValuationRatioHistory: ...


@dataclass(frozen=True, slots=True)
class ToolDeps:
    search: SearchProvider | None = None
    fetcher: PageFetcher | None = None
    coingecko: CoinGeckoClient | None = None
    defillama: DefiLlamaClient | None = None
    hyperliquid: HyperliquidClient | None = None
    sec_edgar: SecEdgarClient | None = None
    fmp: FmpClient | None = None
    clock: Clock | None = None
    bus: EventBus | None = None
    sources: SourceCollector | None = None

    def now(self) -> datetime:
        if self.clock is not None:
            return self.clock.now()
        return datetime.now(UTC)
