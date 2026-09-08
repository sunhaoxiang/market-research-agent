"""给 Agents SDK 用的 `@function_tool` 包装。P4-10 再挂到 Stock Research Agent。"""

from __future__ import annotations

from agents import RunContextWrapper, Tool, function_tool

from agent_service.schemas.tools import ToolResult
from agent_service.tools.deps import ToolDeps
from agent_service.tools.stocks.models import ResolveTickerData
from agent_service.tools.stocks.resolve import run_resolve_ticker
from agent_service.tools.web.collector import stamp_for_agent


@function_tool
async def resolve_ticker(
    ctx: RunContextWrapper[ToolDeps], query: str
) -> ToolResult[ResolveTickerData]:
    """把美股代号、公司名或 CIK 解析成 SEC CIK。后续 SEC / 财务工具请传 ticker 或 CIK。

    同名公司会返回 candidates。能唯一确定时看 resolved.cik；
    无法唯一确定时 resolved 为空，从列表里挑一个 ticker 再调本工具。

    Args:
        query: 代号、公司名或 CIK，例如 "NVDA"、"NVIDIA"、"0001045810"。
    """
    return stamp_for_agent(ctx.context, await run_resolve_ticker(ctx.context, query=query))


STOCK_TOOLS: list[Tool] = [
    resolve_ticker,
]
