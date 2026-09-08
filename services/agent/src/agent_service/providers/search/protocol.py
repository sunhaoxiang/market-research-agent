"""搜索 Provider 的供应商无关契约（§3.5）。

Tavily 是默认实现，但 Agent / Tool 只依赖本协议。换 Exa（语义检索）或
Brave（独立索引）时，新客户端交出同样的 `SearchPage` 即可，不必改 tool 层。

不在这里做 URL 归一化与 `Source.ref` 编号——那是 P2-7 / 编排层的职责。
Provider 只保证：每条 hit 带原始 URL、摘录、以及（若上游给了）清洗后的正文。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from agent_service.schemas.tools import DataProvenance


class SearchTopic(StrEnum):
    GENERAL = "general"
    NEWS = "news"
    FINANCE = "finance"


class SearchTimeRange(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


@dataclass(frozen=True, slots=True)
class SearchQuery:
    query: str
    max_results: int = 5
    time_range: SearchTimeRange | None = None
    include_domains: tuple[str, ...] = ()
    exclude_domains: tuple[str, ...] = ()
    topic: SearchTopic = SearchTopic.GENERAL
    include_raw_content: bool = True
    """Tavily 的核心价值是清洗后的正文（§3.5）。默认开；Exa/Brave 可忽略。"""


@dataclass(frozen=True, slots=True)
class SearchHit:
    url: str
    title: str | None
    snippet: str | None
    raw_content: str | None
    score: float | None
    published_at: datetime | None
    domain: str | None


@dataclass(frozen=True, slots=True)
class SearchPage:
    query: str
    hits: tuple[SearchHit, ...]
    provenance: DataProvenance


@runtime_checkable
class SearchProvider(Protocol):
    name: str

    async def search(self, query: SearchQuery) -> SearchPage: ...

    async def aclose(self) -> None: ...
