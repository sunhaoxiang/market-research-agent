"""Tavily Search 客户端。

认证走 `Authorization: Bearer`，**绝不把 key 放进 JSON body**：旧版 SDK
那样做会让 key 进入缓存键，换 key 写法就让命中率掉到 0，SQLite 里也会
留下 secret 的哈希痕迹。

`search_depth` 钉死 `basic`（1 credit）。`advanced` 要 2 credit，而配额计数
按 HTTP 次计——两者对不上就会在账单之前把额度用超。需要更高召回时再
单独开口子，并让 consume(n=2)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from agent_service.providers.base import BaseProvider
from agent_service.providers.errors import ProviderError
from agent_service.providers.runtime import ProviderRuntime
from agent_service.providers.search.protocol import SearchHit, SearchPage, SearchQuery
from agent_service.providers.ttl import CacheTTL
from agent_service.schemas.tools import ToolErrorCode

_TAVILY_BASE = "https://api.tavily.com"
_MAX_RESULTS = 10
_SEARCH_PATH = "/search"


class TavilySearchProvider(BaseProvider):
    def __init__(
        self,
        *,
        runtime: ProviderRuntime,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        base_url: str = _TAVILY_BASE,
    ) -> None:
        key = api_key.strip()
        if not key:
            raise ProviderError(
                ToolErrorCode.INVALID_INPUT,
                "Tavily API key 为空",
                provider="tavily",
            )
        super().__init__(
            name="tavily",
            base_url=base_url,
            runtime=runtime,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            client=client,
        )

    async def search(self, query: SearchQuery) -> SearchPage:
        q = query.query.strip()
        if not q:
            raise ProviderError(
                ToolErrorCode.INVALID_INPUT,
                "搜索词为空",
                provider=self.name,
                endpoint=_SEARCH_PATH,
            )

        body = _request_body(query, q)
        try:
            response = await self.post_json(_SEARCH_PATH, ttl=CacheTTL.WEB_SEARCH, json_body=body)
        except ProviderError as exc:
            if exc.status_code == httpx.codes.UNAUTHORIZED:
                raise ProviderError(
                    ToolErrorCode.UPSTREAM_ERROR,
                    "Tavily API key 无效或未配置",
                    retryable=False,
                    status_code=exc.status_code,
                    provider=self.name,
                    endpoint=_SEARCH_PATH,
                ) from exc
            raise

        hits = _parse_hits(response.data, provider=self.name)
        return SearchPage(query=q, hits=hits, provenance=response.provenance())


def _request_body(query: SearchQuery, q: str) -> dict[str, object]:
    max_results = min(max(query.max_results, 1), _MAX_RESULTS)
    body: dict[str, object] = {
        "query": q,
        "max_results": max_results,
        "search_depth": "basic",
        "topic": query.topic.value,
        "include_answer": False,
        "include_raw_content": "markdown" if query.include_raw_content else False,
    }
    if query.time_range is not None:
        body["time_range"] = query.time_range.value
    include = _normalize_domains(query.include_domains)
    if include:
        body["include_domains"] = include
    exclude = _normalize_domains(query.exclude_domains)
    if exclude:
        body["exclude_domains"] = exclude
    return body


def _normalize_domains(domains: tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in domains:
        host = raw.strip().lower()
        if host.startswith("www."):
            host = host[4:]
        if not host or host in seen:
            continue
        seen.add(host)
        ordered.append(host)
    ordered.sort()
    return ordered


def _parse_hits(data: Any, *, provider: str) -> tuple[SearchHit, ...]:
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "Tavily 返回了非预期结构（缺少 results）",
            retryable=False,
            provider=provider,
            endpoint=_SEARCH_PATH,
        )
    hits: list[SearchHit] = []
    for item in data["results"]:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        hits.append(
            SearchHit(
                url=url.strip(),
                title=_optional_str(item.get("title")),
                snippet=_optional_str(item.get("content")),
                raw_content=_optional_str(item.get("raw_content")),
                score=_optional_float(item.get("score")),
                published_at=_optional_datetime(
                    item.get("published_date") or item.get("published_at")
                ),
                domain=_domain(url),
            )
        )
    return tuple(hits)


def _optional_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _domain(url: str) -> str | None:
    host = urlparse(url).hostname
    if host is None:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None
