"""会话级 Source 身份：按 canonical URL 去重、编 s1/s2、发 SOURCE_FOUND。

LLM 只看见短引用。真正的 Source 在这里登记，避免模型复述 URL（§15.1）。
同一会话里跨任务共用本对象，引用编号是会话内的，不是每个任务从 s1 重来。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from agent_service.schemas.common import SourceType
from agent_service.schemas.events import SourceFoundEvent, SourceFoundPayload
from agent_service.schemas.sources import Source
from agent_service.sources.canonical import canonicalize_url
from agent_service.sources.reliability import classify_reliability

if TYPE_CHECKING:
    from agent_service.observability.event_bus import EventBus

_EXCERPT_CHARS = 400
_TYPE_RANK = {
    SourceType.WEB: 0,
    SourceType.SOCIAL: 1,
    SourceType.API: 2,
    SourceType.GITHUB: 3,
    SourceType.NEWS: 4,
    SourceType.DOCS: 5,
    SourceType.OFFICIAL: 6,
    SourceType.SEC: 7,
}


@dataclass
class SourceRegistry:
    """一次研究会话的来源账本。"""

    bus: EventBus | None = None
    _by_canonical: dict[str, Source] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)

    def intern(
        self,
        *,
        url: str,
        title: str | None,
        excerpt: str | None,
        provider: str | None,
        retrieved_at: datetime,
        published_at: datetime | None = None,
        source_type: SourceType = SourceType.WEB,
        domain: str | None = None,
        http_status: int | None = None,
    ) -> Source:
        """登记或复用。新来源才发 `SOURCE_FOUND`。"""
        canonical = canonicalize_url(url)
        existing = self._by_canonical.get(canonical)
        if existing is not None:
            self._enrich(
                existing,
                title=title,
                excerpt=excerpt,
                published_at=published_at,
                source_type=source_type,
                http_status=http_status,
                domain=domain,
            )
            return self._by_canonical[canonical]

        host = domain or _domain(canonical) or _domain(url)
        source = Source(
            ref=f"s{len(self._order) + 1}",
            url=url.strip(),
            url_canonical=canonical,
            title=title,
            domain=host,
            source_type=source_type,
            provider=provider,
            reliability=classify_reliability(url, source_type=source_type, domain=host),
            published_at=published_at,
            retrieved_at=retrieved_at,
            excerpt=_excerpt(excerpt),
            http_status=http_status,
        )
        self._by_canonical[canonical] = source
        self._order.append(canonical)
        self._emit(source)
        return source

    def sources(self) -> list[Source]:
        return [self._by_canonical[key] for key in self._order]

    def replace_all(self, sources: list[Source]) -> None:
        """用已编号的副本替换账本。顺序必须与登记顺序一致。"""
        self._by_canonical = {item.url_canonical: item for item in sources}
        self._order = [item.url_canonical for item in sources]

    def _enrich(
        self,
        existing: Source,
        *,
        title: str | None,
        excerpt: str | None,
        published_at: datetime | None,
        source_type: SourceType,
        http_status: int | None,
        domain: str | None,
    ) -> None:
        updates: dict[str, object] = {}
        if title and not existing.title:
            updates["title"] = title
        cleaned = _excerpt(excerpt)
        if cleaned and (existing.excerpt is None or len(cleaned) > len(existing.excerpt)):
            updates["excerpt"] = cleaned
        if published_at is not None and existing.published_at is None:
            updates["published_at"] = published_at
        if http_status is not None and existing.http_status is None:
            updates["http_status"] = http_status
        if _TYPE_RANK[source_type] > _TYPE_RANK[existing.source_type]:
            updates["source_type"] = source_type
            host = existing.domain or domain
            updates["reliability"] = classify_reliability(
                existing.url_canonical, source_type=source_type, domain=host
            )
        if updates:
            self._by_canonical[existing.url_canonical] = existing.model_copy(update=updates)

    def _emit(self, source: Source) -> None:
        if self.bus is None:
            return
        label = source.title or source.domain or source.url
        self.bus.emit(
            SourceFoundEvent,
            payload=SourceFoundPayload(source=source),
            message=f"发现来源：{label}",
        )


def _excerpt(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    if len(cleaned) <= _EXCERPT_CHARS:
        return cleaned
    return cleaned[: _EXCERPT_CHARS - 1] + "…"


def _domain(url: str) -> str | None:
    host = urlsplit(url).hostname
    if host is None:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None
