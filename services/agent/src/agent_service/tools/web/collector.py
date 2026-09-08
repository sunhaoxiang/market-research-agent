"""把 tool 结果登记成 Source，并给 LLM 打上 s1/s2 短引用。

身份在会话级 `SourceRegistry`：同一 canonical URL（含不同跟踪参数）共用
一个 ref。本对象只记住「这个任务见到过哪些」，以免把别的任务的来源塞进
本任务的 finding。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agent_service.schemas.common import SourceType
from agent_service.schemas.tools import ToolError, ToolResult
from agent_service.sources.registry import SourceRegistry
from agent_service.tools.series_metrics import emit_series_metrics
from agent_service.tools.web.models import WebPageData, WebSearchData
from agent_service.tools.web.untrusted import strip_isolation_tags

if TYPE_CHECKING:
    from agent_service.schemas.sources import Source
    from agent_service.tools.deps import ToolDeps


@dataclass
class SourceCollector:
    registry: SourceRegistry = field(default_factory=SourceRegistry)
    _seen: list[str] = field(default_factory=list)
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
        source = self.registry.intern(
            url=url,
            title=title,
            excerpt=strip_isolation_tags(excerpt) if excerpt else excerpt,
            provider=provider,
            retrieved_at=retrieved_at,
            published_at=published_at,
            source_type=source_type,
            domain=domain,
            http_status=http_status,
        )
        if source.url_canonical not in self._seen:
            self._seen.append(source.url_canonical)
        return source.ref

    def sources(self) -> list[Source]:
        by_canonical = {item.url_canonical: item for item in self.registry.sources()}
        return [by_canonical[key] for key in self._seen if key in by_canonical]


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
    url = _structured_url(data)
    if url is None:
        return result
    ref = collector.add(
        url=url,
        title=_structured_title(data),
        excerpt=None,
        provider=provider,
        retrieved_at=retrieved,
        source_type=SourceType.API,
    )
    return result.model_copy(update={"ref": ref})


def stamp_for_agent[T](deps: ToolDeps, result: ToolResult[T]) -> ToolResult[T]:
    """Agent 路径：登记来源，并把 TVL/价格序列打成 METRIC_FOUND。"""
    if deps.sources is None:
        return result
    stamped = stamp_refs(deps.sources, result)
    emit_series_metrics(deps.sources, stamped)
    return stamped


def _structured_url(data: object) -> str | None:
    url = getattr(data, "url", None)
    if isinstance(url, str) and url.strip():
        return url
    resolved = getattr(data, "resolved", None)
    nested = getattr(resolved, "url", None) if resolved is not None else None
    if isinstance(nested, str) and nested.strip():
        return nested
    return None


def _structured_title(data: object) -> str | None:
    for attr in ("name", "title", "chain", "protocol", "coin_id", "query"):
        value = getattr(data, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return None
