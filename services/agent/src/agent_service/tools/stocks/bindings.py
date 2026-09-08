"""给 Agents SDK 用的 `@function_tool` 包装。已挂到 Stock Research Agent。"""

from __future__ import annotations

from agents import RunContextWrapper, Tool, function_tool

from agent_service.schemas.tools import ToolResult
from agent_service.tools.deps import ToolDeps
from agent_service.tools.stocks.market import (
    run_compare_to_index,
    run_get_company_profile,
    run_get_peers,
    run_get_stock_price_history,
    run_get_stock_quote,
)
from agent_service.tools.stocks.models import (
    IndexCompareData,
    ResolveTickerData,
    StockPeersData,
    StockPriceHistoryData,
    StockProfileData,
    StockQuoteData,
)
from agent_service.tools.stocks.resolve import run_resolve_ticker
from agent_service.tools.web.collector import stamp_for_agent


@function_tool
async def resolve_ticker(
    ctx: RunContextWrapper[ToolDeps], query: str
) -> ToolResult[ResolveTickerData]:
    """把美股代号、公司名或 CIK 解析成 SEC CIK。后续行情工具请传 ticker，不要再搜公司名。

    同名公司会返回 candidates。能唯一确定时看 resolved.ticker；
    无法唯一确定时 resolved 为空，从列表里挑一个 ticker 再调本工具。

    Args:
        query: 代号、公司名或 CIK，例如 "NVDA"、"NVIDIA"、"0001045810"。
    """
    return stamp_for_agent(ctx.context, await run_resolve_ticker(ctx.context, query=query))


@function_tool
async def get_stock_quote(
    ctx: RunContextWrapper[ToolDeps], ticker: str
) -> ToolResult[StockQuoteData]:
    """查询美股现价。ticker 必须是 resolve_ticker 返回的代号，不要传公司名。

    Args:
        ticker: 美股代号，例如 "NVDA"。
    """
    return stamp_for_agent(ctx.context, await run_get_stock_quote(ctx.context, ticker=ticker))


@function_tool
async def get_company_profile(
    ctx: RunContextWrapper[ToolDeps], ticker: str
) -> ToolResult[StockProfileData]:
    """查询公司简介（行业、交易所、员工数等）。ticker 必须是代号。

    Args:
        ticker: 美股代号，例如 "NVDA"。
    """
    return stamp_for_agent(ctx.context, await run_get_company_profile(ctx.context, ticker=ticker))


@function_tool
async def get_stock_price_history(
    ctx: RunContextWrapper[ToolDeps], ticker: str, days: int = 30
) -> ToolResult[StockPriceHistoryData]:
    """查询日线收盘价序列。不要与加密的 get_price_history 混用。

    Args:
        ticker: 美股代号，例如 "NVDA"。
        days: 回看天数，1–1825，默认 30。
    """
    return stamp_for_agent(
        ctx.context, await run_get_stock_price_history(ctx.context, ticker=ticker, days=days)
    )


@function_tool
async def get_peers(ctx: RunContextWrapper[ToolDeps], ticker: str) -> ToolResult[StockPeersData]:
    """查询 FMP 给出的可比公司。列表可能为空，看 quality.missing_fields，不要编造同行。

    Args:
        ticker: 美股代号，例如 "NVDA"。
    """
    return stamp_for_agent(ctx.context, await run_get_peers(ctx.context, ticker=ticker))


@function_tool
async def compare_to_index(
    ctx: RunContextWrapper[ToolDeps],
    ticker: str,
    index: str = "SPY",
    days: int = 30,
) -> ToolResult[IndexCompareData]:
    """把标的同期收益与指数（默认 SPY）相减，得到超额收益。算术在 Python 里算，不要心算。

    Args:
        ticker: 美股代号，例如 "NVDA"。
        index: 指数或 ETF 代号，默认 SPY。
        days: 回看天数，1–1825，默认 30。
    """
    return stamp_for_agent(
        ctx.context,
        await run_compare_to_index(ctx.context, ticker=ticker, index=index, days=days),
    )


STOCK_TOOLS: list[Tool] = [
    resolve_ticker,
    get_stock_quote,
    get_company_profile,
    get_stock_price_history,
    get_peers,
    compare_to_index,
]
