"""按名字调度 tool。HTTP invoke 与 eval 都走这里，不经过 LLM。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from agent_service.schemas.tools import ToolResult
from agent_service.tools._result import fail_validation
from agent_service.tools.deps import ToolDeps
from agent_service.tools.web.fetch import run_web_fetch
from agent_service.tools.web.models import WebPageData, WebSearchData
from agent_service.tools.web.search import run_news_search, run_web_search

type ToolHandler = Callable[[ToolDeps, dict[str, Any]], Awaitable[ToolResult[Any]]]


class WebSearchArgs(BaseModel):
    query: str
    max_results: int = 5
    time_range: str | None = None
    include_domains: list[str] = Field(default_factory=list)


class NewsSearchArgs(BaseModel):
    query: str
    symbols: list[str] = Field(default_factory=list)
    since: str | None = None


class WebFetchArgs(BaseModel):
    url: str


async def _web_search(deps: ToolDeps, arguments: dict[str, Any]) -> ToolResult[WebSearchData]:
    try:
        args = WebSearchArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("web_search", exc)
    return await run_web_search(
        deps,
        query=args.query,
        max_results=args.max_results,
        time_range=args.time_range,
        include_domains=args.include_domains,
    )


async def _news_search(deps: ToolDeps, arguments: dict[str, Any]) -> ToolResult[WebSearchData]:
    try:
        args = NewsSearchArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("news_search", exc)
    return await run_news_search(deps, query=args.query, symbols=args.symbols, since=args.since)


async def _web_fetch(deps: ToolDeps, arguments: dict[str, Any]) -> ToolResult[WebPageData]:
    try:
        args = WebFetchArgs.model_validate(arguments)
    except ValidationError as exc:
        return fail_validation("web_fetch", exc)
    return await run_web_fetch(deps, url=args.url)


HANDLERS: dict[str, ToolHandler] = {
    "news_search": _news_search,
    "web_fetch": _web_fetch,
    "web_search": _web_search,
}


async def invoke_tool(name: str, arguments: dict[str, Any], deps: ToolDeps) -> ToolResult[Any]:
    handler = HANDLERS[name]
    return await handler(deps, arguments)
