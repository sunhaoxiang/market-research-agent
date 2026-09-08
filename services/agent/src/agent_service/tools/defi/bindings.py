"""给 Agents SDK 用的 `@function_tool` 包装。P3-9 再挂到 Crypto Research Agent。"""

from __future__ import annotations

from agents import RunContextWrapper, Tool, function_tool

from agent_service.schemas.tools import ToolResult
from agent_service.tools.defi.llama import (
    run_get_chain_overview,
    run_get_dex_volume,
    run_get_protocol_fees_revenue,
    run_get_tvl,
)
from agent_service.tools.defi.models import (
    ChainOverviewData,
    DexVolumeData,
    FeesRevenueData,
    TvlData,
)
from agent_service.tools.deps import ToolDeps
from agent_service.tools.web.collector import stamp_for_agent


@function_tool
async def get_tvl(
    ctx: RunContextWrapper[ToolDeps],
    protocol: str | None = None,
    chain: str | None = None,
    days: int = 30,
) -> ToolResult[TvlData]:
    """查询协议或链的 TVL 与近期序列。protocol 与 chain 只填一个。

    Args:
        protocol: DefiLlama 协议 slug，例如 "hyperliquid"。
        chain: DefiLlama 链名，大小写需与官网一致，例如 "Hyperliquid"。
        days: 回看天数，1–365，默认 30。在本地裁切，不另打一枪。
    """
    return stamp_for_agent(
        ctx.context, await run_get_tvl(ctx.context, protocol=protocol, chain=chain, days=days)
    )


@function_tool
async def get_protocol_fees_revenue(
    ctx: RunContextWrapper[ToolDeps], protocol: str
) -> ToolResult[FeesRevenueData]:
    """查询协议手续费与收入（24h / 7d / 30d）。没有 revenue adapter 时对应字段为空。

    Args:
        protocol: DefiLlama 协议 slug，例如 "hyperliquid"。
    """
    return stamp_for_agent(
        ctx.context, await run_get_protocol_fees_revenue(ctx.context, protocol=protocol)
    )


@function_tool
async def get_dex_volume(
    ctx: RunContextWrapper[ToolDeps], protocol: str
) -> ToolResult[DexVolumeData]:
    """查询 DEX 成交量。目前只支持协议 slug，没有按链的 DefiLlama 汇总。

    Args:
        protocol: DefiLlama 协议 slug，例如 "hyperliquid"。
    """
    return stamp_for_agent(ctx.context, await run_get_dex_volume(ctx.context, protocol=protocol))


@function_tool
async def get_chain_overview(
    ctx: RunContextWrapper[ToolDeps], chain: str
) -> ToolResult[ChainOverviewData]:
    """查询链生态概览（TVL、原生代币符号、CoinGecko id）。

    Args:
        chain: DefiLlama 链名，例如 "Hyperliquid"。大小写不敏感。
    """
    return stamp_for_agent(ctx.context, await run_get_chain_overview(ctx.context, chain=chain))


DEFI_TOOLS: list[Tool] = [
    get_tvl,
    get_protocol_fees_revenue,
    get_dex_volume,
    get_chain_overview,
]
