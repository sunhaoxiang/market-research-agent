"""给 Agents SDK 用的 `@function_tool` 包装。P4-10 再挂到 Stock Research Agent。"""

from __future__ import annotations

from agents import RunContextWrapper, Tool, function_tool

from agent_service.schemas.tools import ToolResult
from agent_service.tools.deps import ToolDeps
from agent_service.tools.sec.earnings import run_get_earnings_summary
from agent_service.tools.sec.facts import run_get_xbrl_facts
from agent_service.tools.sec.filings import run_list_sec_filings
from agent_service.tools.sec.models import (
    EarningsSummaryData,
    FilingSectionData,
    SecFilingsData,
    XbrlFactsData,
)
from agent_service.tools.sec.sections import run_get_filing_section
from agent_service.tools.web.collector import stamp_for_agent


@function_tool
async def list_sec_filings(
    ctx: RunContextWrapper[ToolDeps],
    ticker: str,
    form_types: list[str] | None = None,
    limit: int = 10,
) -> ToolResult[SecFilingsData]:
    """列出该公司最近的 SEC 申报。默认 10-K / 10-Q / 8-K。ticker 必须是代号。

    Args:
        ticker: 美股代号，例如 "NVDA"。
        form_types: 表类型，例如 ["10-K", "10-Q"]。空则用默认。
        limit: 返回条数，1–40，默认 10。
    """
    return stamp_for_agent(
        ctx.context,
        await run_list_sec_filings(ctx.context, ticker=ticker, form_types=form_types, limit=limit),
    )


@function_tool
async def get_filing_section(
    ctx: RunContextWrapper[ToolDeps],
    accession: str,
    section: str,
) -> ToolResult[FilingSectionData]:
    """取出一份已申报文件的指定章节正文（10-K Item 1A / 7，10-Q Item 2）。只要 accession。

    Args:
        accession: 申报编号，例如 "0001045810-25-000031"。
        section: 章节，例如 "1A"、"risk_factors"、"7"、"mda"、"2"。
    """
    return stamp_for_agent(
        ctx.context,
        await run_get_filing_section(ctx.context, accession=accession, section=section),
    )


@function_tool
async def get_xbrl_facts(
    ctx: RunContextWrapper[ToolDeps],
    ticker: str,
    concepts: list[str],
) -> ToolResult[XbrlFactsData]:
    """按 us-gaap（或 taxonomy:tag）取出 XBRL 事实。缺 tag 是 None，不要当成 0。

    Args:
        ticker: 美股代号，例如 "NVDA"。
        concepts: 例如 ["Revenues"] 或 ["us-gaap:NetIncomeLoss"]。
    """
    return stamp_for_agent(
        ctx.context,
        await run_get_xbrl_facts(ctx.context, ticker=ticker, concepts=concepts),
    )


@function_tool
async def get_earnings_summary(
    ctx: RunContextWrapper[ToolDeps],
    ticker: str,
) -> ToolResult[EarningsSummaryData]:
    """最近一季（没有季报则用年报）的营收、经营利润、净利和稀释 EPS。不拉 10-K HTML。

    Args:
        ticker: 美股代号，例如 "NVDA"。
    """
    return stamp_for_agent(ctx.context, await run_get_earnings_summary(ctx.context, ticker=ticker))


SEC_TOOLS: list[Tool] = [
    list_sec_filings,
    get_filing_section,
    get_xbrl_facts,
    get_earnings_summary,
]
