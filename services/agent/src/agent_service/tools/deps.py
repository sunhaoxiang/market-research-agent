"""Tool 运行时依赖。

Provider 由 lifespan 注入，再经 `RunContextWrapper.context` 传给
`@function_tool`。HTTP `/v1/tools/{name}/invoke` 走同一份 `ToolDeps`，
这样调试路径和 Agent 路径不会出现「一边有 key、一边没有」的分叉。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_service.observability.event_bus import EventBus
    from agent_service.providers.fetch import PageFetcher
    from agent_service.providers.runtime import Clock
    from agent_service.providers.search import SearchProvider
    from agent_service.tools.web.collector import SourceCollector


@dataclass(frozen=True, slots=True)
class ToolDeps:
    search: SearchProvider | None = None
    fetcher: PageFetcher | None = None
    clock: Clock | None = None
    bus: EventBus | None = None
    sources: SourceCollector | None = None

    def now(self) -> datetime:
        if self.clock is not None:
            return self.clock.now()
        return datetime.now(UTC)
