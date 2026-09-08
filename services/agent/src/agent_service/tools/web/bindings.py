"""给 Agents SDK 用的 `@function_tool` 包装。

实现在 `run_*`：HTTP invoke 和单测直接调那些函数。这里只负责
从 `RunContextWrapper` 取出 `ToolDeps`、把 docstring 暴露给模型，
以及把正文包进 `<untrusted_web_content>`、打上 s1/s2（P2-5 / P2-6）。
invoke 仍返回原文，方便核对抽取结果；模型只看见带标签和短引用的那一份。
"""

from __future__ import annotations

from agents import RunContextWrapper, Tool, function_tool

from agent_service.schemas.tools import ToolResult
from agent_service.tools.deps import ToolDeps
from agent_service.tools.web.collector import stamp_refs
from agent_service.tools.web.fetch import run_web_fetch
from agent_service.tools.web.models import WebPageData, WebSearchData
from agent_service.tools.web.search import run_news_search, run_web_search
from agent_service.tools.web.untrusted import wrap_result_for_llm


@function_tool
async def web_search(
    ctx: RunContextWrapper[ToolDeps],
    query: str,
    max_results: int = 5,
    time_range: str | None = None,
    include_domains: list[str] | None = None,
) -> ToolResult[WebSearchData]:
    """搜索公开网页，返回带出处的结果列表。

    Args:
        query: 搜索词。尽量带标的全名与时间范围，例如 "Hyperliquid HYPE fee switch 2026"。
        max_results: 返回条数，1–10，默认 5。
        time_range: 只保留最近一段时间的结果。取值 day / week / month / year。
        include_domains: 只搜索这些域名，例如 ["sec.gov", "hyperliquid.xyz"]。
    """
    return _for_agent(
        ctx.context,
        await run_web_search(
            ctx.context,
            query=query,
            max_results=max_results,
            time_range=time_range,
            include_domains=include_domains,
        ),
    )


@function_tool
async def news_search(
    ctx: RunContextWrapper[ToolDeps],
    query: str,
    symbols: list[str] | None = None,
    since: str | None = None,
) -> ToolResult[WebSearchData]:
    """搜索新闻源，适合找近期公告、报道与事件时间线。

    Args:
        query: 事件或主题，例如 "Hyperliquid listing rumor"。
        symbols: 相关代号，会并入搜索词，例如 ["HYPE"]。
        since: 时间下界。取值 day / week / month / year，或 ISO 日期（如 2026-09-01）。
    """
    return _for_agent(
        ctx.context,
        await run_news_search(ctx.context, query=query, symbols=symbols, since=since),
    )


@function_tool
async def web_fetch(ctx: RunContextWrapper[ToolDeps], url: str) -> ToolResult[WebPageData]:
    """抓取单个 URL 的正文。只接受 http/https 的公网地址。

    Args:
        url: 要读取的页面地址。内网、云 metadata、超大响应会被拒绝。
    """
    return _for_agent(ctx.context, await run_web_fetch(ctx.context, url=url))


def _for_agent[T](deps: ToolDeps, result: ToolResult[T]) -> ToolResult[T]:
    """Agent 看到的那一份：打上 s1/s2，正文进隔离标签。invoke 不走这里。"""
    prepared = result
    if deps.sources is not None:
        prepared = stamp_refs(deps.sources, prepared)
    return wrap_result_for_llm(prepared)


WEB_TOOLS: list[Tool] = [web_search, news_search, web_fetch]
