"""搜索 Provider：协议 + Tavily 实现。Exa / Brave 接入时放在同目录。"""

from agent_service.providers.search.protocol import (
    SearchHit,
    SearchPage,
    SearchProvider,
    SearchQuery,
    SearchTimeRange,
    SearchTopic,
)
from agent_service.providers.search.tavily import TavilySearchProvider

__all__ = [
    "SearchHit",
    "SearchPage",
    "SearchProvider",
    "SearchQuery",
    "SearchTimeRange",
    "SearchTopic",
    "TavilySearchProvider",
]
