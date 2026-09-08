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
    from agent_service.providers.fetch import PageFetcher
    from agent_service.providers.runtime import Clock
    from agent_service.providers.search import SearchProvider
    from agent_service.tools.web.collector import SourceCollector


class CoinGeckoClient(Protocol):
    """CoinGecko 在 ToolDeps 上的面。P3-3 用 search；P3-4 用行情方法。假客户端按需实现。"""

    async def search_coins(self, query: str) -> CoinSearchPage: ...

    async def get_price(self, coin_id: str, *, vs_currency: str = "usd") -> CoinPrice: ...

    async def get_market(self, coin_id: str, *, vs_currency: str = "usd") -> CoinMarket: ...

    async def get_market_chart(
        self, coin_id: str, *, days: int = 30, vs_currency: str = "usd"
    ) -> MarketChart: ...


@dataclass(frozen=True, slots=True)
class ToolDeps:
    search: SearchProvider | None = None
    fetcher: PageFetcher | None = None
    coingecko: CoinGeckoClient | None = None
    clock: Clock | None = None
    bus: EventBus | None = None
    sources: SourceCollector | None = None

    def now(self) -> datetime:
        if self.clock is not None:
            return self.clock.now()
        return datetime.now(UTC)
