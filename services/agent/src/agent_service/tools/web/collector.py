"""把 tool 结果登记成 Source，并给 LLM 打上 s1/s2 短引用。

URL 归一化与 reliability 分级是 P2-7 的事。这里只保证：同一次任务里
同一个 URL 共用一个 ref，LLM 不必（也不该）自己复述 URL（§15.1）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse

from agent_service.schemas.common import SourceReliability, SourceType
from agent_service.schemas.sources import Source
from agent_service.schemas.tools import ToolError, ToolResult
from agent_service.tools.web.models import WebPageData, WebSearchData
from agent_service.tools.web.untrusted import strip_isolation_tags

_EXCERPT_CHARS = 400


@dataclass
class SourceCollector:
    _by_url: dict[str, Source] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)
    errors: list[ToolError] = field(default_factory=list)

    def add(
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
    ) -> str:
        key = url.strip()
        existing = self._by_url.get(key)
        if existing is not None:
            return existing.ref
        ref = f"s{len(self._order) + 1}"
        source = Source(
            ref=ref,
            url=key,
            url_canonical=key,
            title=title,
            domain=domain or _domain(key),
            source_type=source_type,
            provider=provider,
            reliability=SourceReliability.UNKNOWN,
            published_at=published_at,
            retrieved_at=retrieved_at,
            excerpt=_excerpt(excerpt),
            http_status=http_status,
        )
        self._by_url[key] = source
        self._order.append(key)
        return ref

    def sources(self) -> list[Source]:
        return [self._by_url[url] for url in self._order]


def stamp_refs[T](collector: SourceCollector, result: ToolResult[T]) -> ToolResult[T]:
    """成功则给每条 hit/页面打 ref；失败则记下 ToolError。不改正文。"""
    if not result.ok:
        if result.error is not None:
            collector.errors.append(result.error)
        return result
    if result.data is None:
        return result
    retrieved = (
        result.provenance.retrieved_at if result.provenance is not None else datetime.now(UTC)
    )
    provider = result.provenance.provider if result.provenance is not None else None
    data = result.data
    if isinstance(data, WebSearchData):
        source_type = SourceType.NEWS if data.topic == "news" else SourceType.WEB
        hits = [
            hit.model_copy(
                update={
                    "ref": collector.add(
                        url=hit.url,
                        title=hit.title,
                        excerpt=hit.snippet,
                        provider=provider,
                        retrieved_at=retrieved,
                        published_at=hit.published_at,
                        source_type=source_type,
                        domain=hit.domain,
                    )
                }
            )
            for hit in data.hits
        ]
        return result.model_copy(update={"data": data.model_copy(update={"hits": hits})})
    if isinstance(data, WebPageData):
        ref = collector.add(
            url=data.final_url or data.url,
            title=data.title,
            excerpt=data.text,
            provider=provider,
            retrieved_at=retrieved,
            source_type=SourceType.WEB,
            http_status=data.status_code,
        )
        return result.model_copy(update={"data": data.model_copy(update={"ref": ref})})
    return result


def _excerpt(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = strip_isolation_tags(text).strip()
    if not cleaned:
        return None
    if len(cleaned) <= _EXCERPT_CHARS:
        return cleaned
    return cleaned[: _EXCERPT_CHARS - 1] + "…"


def _domain(url: str) -> str | None:
    host = urlparse(url).hostname
    if host is None:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None
