"""给 Agents SDK 用的 `@function_tool` 包装。P4-10 再挂到 Stock Research Agent。"""

from __future__ import annotations

from agents import RunContextWrapper, Tool, function_tool

from agent_service.schemas.tools import ToolResult
from agent_service.tools.deps import ToolDeps
from agent_service.tools.financials.growth import run_get_growth_metrics
from agent_service.tools.financials.models import (
    BalanceSheetData,
    CashFlowData,
    GrowthMetricsData,
    IncomeStatementData,
)
from agent_service.tools.financials.statements import (
    run_get_balance_sheet,
    run_get_cash_flow,
    run_get_income_statement,
)
from agent_service.tools.web.collector import stamp_for_agent


@function_tool
async def get_income_statement(
    ctx: RunContextWrapper[ToolDeps],
    ticker: str,
    period: str = "annual",
    limit: int = 4,
) -> ToolResult[IncomeStatementData]:
    """查询利润表。优先用 SEC XBRL 官方数字；缺期间才退回 FMP。ticker 必须是代号。

    Args:
        ticker: 美股代号，例如 "NVDA"。
        period: annual 或 quarterly，默认 annual。
        limit: 最近几期，1–20，默认 4。
    """
    return stamp_for_agent(
        ctx.context,
        await run_get_income_statement(ctx.context, ticker=ticker, period=period, limit=limit),
    )


@function_tool
async def get_balance_sheet(
    ctx: RunContextWrapper[ToolDeps],
    ticker: str,
    period: str = "annual",
    limit: int = 4,
) -> ToolResult[BalanceSheetData]:
    """查询资产负债表。优先 SEC XBRL，缺期间才退回 FMP。ticker 必须是代号。

    Args:
        ticker: 美股代号，例如 "NVDA"。
        period: annual 或 quarterly，默认 annual。
        limit: 最近几期，1–20，默认 4。
    """
    return stamp_for_agent(
        ctx.context,
        await run_get_balance_sheet(ctx.context, ticker=ticker, period=period, limit=limit),
    )


@function_tool
async def get_cash_flow(
    ctx: RunContextWrapper[ToolDeps],
    ticker: str,
    period: str = "annual",
    limit: int = 4,
) -> ToolResult[CashFlowData]:
    """查询现金流量表。优先 SEC XBRL，缺期间才退回 FMP。ticker 必须是代号。

    Args:
        ticker: 美股代号，例如 "NVDA"。
        period: annual 或 quarterly，默认 annual。
        limit: 最近几期，1–20，默认 4。
    """
    return stamp_for_agent(
        ctx.context,
        await run_get_cash_flow(ctx.context, ticker=ticker, period=period, limit=limit),
    )


@function_tool
async def get_growth_metrics(
    ctx: RunContextWrapper[ToolDeps],
    ticker: str,
    years: int = 3,
) -> ToolResult[GrowthMetricsData]:
    """从利润表计算营收/利润的 YoY、QoQ、CAGR 和利润率走势。算术在 Python 里完成。

    Args:
        ticker: 美股代号，例如 "NVDA"。
        years: CAGR 回看年数，1–10，默认 3。
    """
    return stamp_for_agent(
        ctx.context,
        await run_get_growth_metrics(ctx.context, ticker=ticker, years=years),
    )


FINANCIALS_TOOLS: list[Tool] = [
    get_income_statement,
    get_balance_sheet,
    get_cash_flow,
    get_growth_metrics,
]
