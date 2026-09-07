"""外部数据源客户端。

P2-1 只落地横切基础设施（缓存 / 限流 / 配额 / 重试）。具体源（Tavily、
CoinGecko 等）从 P2-2 / P3 / P4 接入时继承 `BaseProvider` 即可。
"""

from agent_service.providers.base import (
    BaseProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderStats,
)
from agent_service.providers.errors import ProviderError, RetryableProviderError
from agent_service.providers.profiles import PROFILES, ProviderProfile, RetryPolicy, profile_for
from agent_service.providers.runtime import Clock, ProviderRuntime
from agent_service.providers.ttl import CacheTTL

__all__ = [
    "PROFILES",
    "BaseProvider",
    "CacheTTL",
    "Clock",
    "ProviderError",
    "ProviderProfile",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderRuntime",
    "ProviderStats",
    "RetryPolicy",
    "RetryableProviderError",
    "profile_for",
]
