"""给 Agents SDK 用的 `@function_tool` 包装。P3-9 再挂到 Crypto Research Agent。"""

from __future__ import annotations

from agents import RunContextWrapper, Tool, function_tool

from agent_service.schemas.tools import ToolResult
from agent_service.tools.deps import ToolDeps
from agent_service.tools.onchain.activity import run_get_chain_activity
from agent_service.tools.onchain.gaps import (
    run_get_exchange_flow,
    run_get_token_holders,
    run_get_whale_activity,
)
from agent_service.tools.onchain.models import ChainActivityData
from agent_service.tools.web.collector import stamp_for_agent


@function_tool
async def get_chain_activity(
    ctx: RunContextWrapper[ToolDeps], chain: str, days: int = 1
) -> ToolResult[ChainActivityData]:
    """查询链上活动。目前只有 Hyperliquid 的 24h 永续成交量与持仓；活跃地址和交易数通常没有。

    Args:
        chain: 链名，例如 "Hyperliquid"。其它链会返回 unsupported。
        days: 回看天数，1–365。Hyperliquid 只有 24h 快照，大于 1 时不会当成历史序列。
    """
    return stamp_for_agent(
        ctx.context, await run_get_chain_activity(ctx.context, chain=chain, days=days)
    )


@function_tool
async def get_token_holders(ctx: RunContextWrapper[ToolDeps], asset: str) -> ToolResult[None]:
    """查询代币持有人分布。免费源没有这项数据，会返回 unsupported，不要编造。

    Args:
        asset: 资产代号或 coin id，例如 "hyperliquid"。
    """
    return stamp_for_agent(ctx.context, await run_get_token_holders(ctx.context, asset=asset))


@function_tool
async def get_whale_activity(
    ctx: RunContextWrapper[ToolDeps], asset: str, threshold: float | None = None
) -> ToolResult[None]:
    """查询巨鲸转账。免费源没有这项数据，会返回 unsupported，不要编造。

    Args:
        asset: 资产代号或 coin id，例如 "hyperliquid"。
        threshold: 金额门槛（USD）。当前无数据源，参数会被忽略。
    """
    return stamp_for_agent(
        ctx.context, await run_get_whale_activity(ctx.context, asset=asset, threshold=threshold)
    )


@function_tool
async def get_exchange_flow(
    ctx: RunContextWrapper[ToolDeps], asset: str, days: int = 30
) -> ToolResult[None]:
    """查询交易所净流入/流出。免费源没有这项数据，会返回 unsupported，不要编造。

    Args:
        asset: 资产代号或 coin id，例如 "hyperliquid"。
        days: 回看天数。当前无数据源，参数会被忽略。
    """
    return stamp_for_agent(
        ctx.context, await run_get_exchange_flow(ctx.context, asset=asset, days=days)
    )


ONCHAIN_TOOLS: list[Tool] = [
    get_chain_activity,
    get_token_holders,
    get_whale_activity,
    get_exchange_flow,
]
