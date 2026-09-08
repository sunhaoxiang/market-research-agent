"""web_search / news_search：SearchProvider → ToolResult。"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_service.providers.errors import ProviderError
from agent_service.providers.search import (
    SearchPage,
    SearchQuery,
    SearchTimeRange,
    SearchTopic,
)
from agent_service.schemas.tools import DataQuality, ToolResult
from agent_service.tools._result import fail_invalid, fail_provider, fail_unavailable
from agent_service.tools.deps import ToolDeps
from agent_service.tools.web.models import WebSearchData, WebSearchHit

_MAX_RESULTS = 10
_TIME_RANGES = {item.value: item for item in SearchTimeRange}
_SINCE_WINDOWS: tuple[tuple[int, SearchTimeRange], ...] = (
    (1, SearchTimeRange.DAY),
    (7, SearchTimeRange.WEEK),
    (31, SearchTimeRange.MONTH),
)


def _clamp_max_results(value: int) -> int:
    return min(max(value, 1), _MAX_RESULTS)


def _parse_time_range(raw: str | None) -> SearchTimeRange | None:
    if raw is None:
        return None
    key = raw.strip().lower()
    if not key:
        return None
    parsed = _TIME_RANGES.get(key)
    if parsed is None:
        allowed = " / ".join(_TIME_RANGES)
        raise ValueError(f"time_range 只能是 {allowed}，收到 {raw!r}")
    return parsed


def parse_since(raw: str | None, *, now: datetime) -> SearchTimeRange | None:
    """`since` 既可以是 day/week/month/year，也可以是 ISO 日期。

    ISO 日期映射到能覆盖该时点的最小 Tavily 窗口——上游没有任意起点，
    只能近似。窗口偏大总比把更早的结果裁掉好。
    """
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None
    named = _TIME_RANGES.get(key.lower())
    if named is not None:
        return named
    try:
        parsed = datetime.fromisoformat(key.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"无法解析 since: {raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if parsed > now:
        raise ValueError("since 不能是未来时间")
    days = (now - parsed).days
    for limit, window in _SINCE_WINDOWS:
        if days <= limit:
            return window
    return SearchTimeRange.YEAR


def _hits(page: SearchPage) -> list[WebSearchHit]:
    return [
        WebSearchHit(
            url=hit.url,
            title=hit.title,
            snippet=hit.snippet,
            raw_content=hit.raw_content,
            score=hit.score,
            published_at=hit.published_at,
            domain=hit.domain,
        )
        for hit in page.hits
    ]


def _quality(hits: list[WebSearchHit]) -> DataQuality | None:
    if hits:
        return None
    return DataQuality(
        completeness="partial",
        missing_fields=["hits"],
        caveats=["搜索无结果"],
    )


def _unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in values:
        token = raw.strip()
        key = token.lower()
        if not token or key in seen:
            continue
        seen.add(key)
        ordered.append(token)
    return ordered


def _compose_news_query(query: str, symbols: list[str]) -> str:
    parts = [query.strip()]
    seen = {query.strip().lower()}
    for token in symbols:
        if token.lower() in seen:
            continue
        seen.add(token.lower())
        parts.append(token)
    return " ".join(parts)


async def run_web_search(
    deps: ToolDeps,
    *,
    query: str,
    max_results: int = 5,
    time_range: str | None = None,
    include_domains: list[str] | None = None,
) -> ToolResult[WebSearchData]:
    q = query.strip()
    if not q:
        return fail_invalid("web_search", "搜索词为空")
    if deps.search is None:
        return fail_unavailable(
            tool="web_search",
            provider="tavily",
            message="搜索服务未配置：缺少 TAVILY_API_KEY",
        )
    try:
        parsed_range = _parse_time_range(time_range)
    except ValueError as exc:
        return fail_invalid("web_search", str(exc))

    search_query = SearchQuery(
        query=q,
        max_results=_clamp_max_results(max_results),
        time_range=parsed_range,
        include_domains=tuple(d.strip() for d in include_domains or [] if d.strip()),
        topic=SearchTopic.GENERAL,
    )
    try:
        page = await deps.search.search(search_query)
    except ProviderError as exc:
        return fail_provider("web_search", exc)

    hits = _hits(page)
    return ToolResult.success(
        WebSearchData(query=page.query, hits=hits, topic=SearchTopic.GENERAL.value),
        provenance=page.provenance,
        quality=_quality(hits),
    )


async def run_news_search(
    deps: ToolDeps,
    *,
    query: str,
    symbols: list[str] | None = None,
    since: str | None = None,
) -> ToolResult[WebSearchData]:
    q = query.strip()
    if not q:
        return fail_invalid("news_search", "搜索词为空")
    if deps.search is None:
        return fail_unavailable(
            tool="news_search",
            provider="tavily",
            message="搜索服务未配置：缺少 TAVILY_API_KEY",
        )
    cleaned_symbols = _unique_keep_order(list(symbols or []))
    try:
        time_range = parse_since(since, now=deps.now())
    except ValueError as exc:
        return fail_invalid("news_search", str(exc))

    search_query = SearchQuery(
        query=_compose_news_query(q, cleaned_symbols),
        max_results=5,
        time_range=time_range,
        topic=SearchTopic.NEWS,
    )
    try:
        page = await deps.search.search(search_query)
    except ProviderError as exc:
        return fail_provider("news_search", exc)

    hits = _hits(page)
    return ToolResult.success(
        WebSearchData(
            query=page.query,
            hits=hits,
            topic=SearchTopic.NEWS.value,
            symbols=cleaned_symbols,
        ),
        provenance=page.provenance,
        quality=_quality(hits),
    )
