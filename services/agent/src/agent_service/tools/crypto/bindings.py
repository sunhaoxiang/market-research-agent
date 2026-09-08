"""给 Agents SDK 用的 `@function_tool` 包装。P3-9 再挂到 Crypto Research Agent。"""

from __future__ import annotations

from agents import RunContextWrapper, Tool, function_tool

from agent_service.schemas.tools import ToolResult
from agent_service.tools.crypto.models import ResolveAssetData
from agent_service.tools.crypto.resolve import run_resolve_asset
from agent_service.tools.deps import ToolDeps


@function_tool
async def resolve_asset(
    ctx: RunContextWrapper[ToolDeps], query: str
) -> ToolResult[ResolveAssetData]:
    """把代币代号或名称解析成 CoinGecko coin id。后续行情工具请传 coin_id，不要再传代号。

    同名代币会返回 candidates。若已按市值排名自动选取，看 resolved.coin_id；
    无法唯一确定时 resolved 为空，从列表里挑一个 id 再调本工具。

    Args:
        query: 代号、全名或 coin id，例如 "HYPE"、"Hyperliquid"、"hyperliquid"。
    """
    return await run_resolve_asset(ctx.context, query=query)


CRYPTO_TOOLS: list[Tool] = [resolve_asset]
