"""外部数据源客户端。

横切能力在 `BaseProvider`；具体源按协议接入。搜索走 `SearchProvider`，
默认实现是 Tavily，接口按可替换 Exa / Brave 设计（§3.5）。
"""

from agent_service.providers.base import (
    BaseProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderStats,
)
from agent_service.providers.crypto import CoinGeckoProvider
from agent_service.providers.errors import ProviderError, RetryableProviderError
from agent_service.providers.fetch import FetchedPage, PageFetcher, WebFetcher
from agent_service.providers.profiles import PROFILES, ProviderProfile, RetryPolicy, profile_for
from agent_service.providers.runtime import Clock, ProviderRuntime
from agent_service.providers.search import (
    SearchHit,
    SearchPage,
    SearchProvider,
    SearchQuery,
    TavilySearchProvider,
)
from agent_service.providers.ttl import CacheTTL

__all__ = [
    "PROFILES",
    "BaseProvider",
    "CacheTTL",
    "Clock",
    "CoinGeckoProvider",
    "FetchedPage",
    "PageFetcher",
    "ProviderError",
    "ProviderProfile",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderRuntime",
    "ProviderStats",
    "RetryPolicy",
    "RetryableProviderError",
    "SearchHit",
    "SearchPage",
    "SearchProvider",
    "SearchQuery",
    "TavilySearchProvider",
    "WebFetcher",
    "profile_for",
]
