"""给 Agents SDK 用的 `@function_tool` 包装。P3-9 再挂到 Crypto Research Agent。"""

from __future__ import annotations

from agents import RunContextWrapper, Tool, function_tool

from agent_service.schemas.tools import ToolResult
from agent_service.tools.crypto.market import (
    run_get_crypto_price,
    run_get_market_data,
    run_get_price_history,
)
from agent_service.tools.crypto.models import (
    CryptoMarketData,
    CryptoPriceData,
    CryptoPriceHistoryData,
    ResolveAssetData,
    TokenomicsData,
)
from agent_service.tools.crypto.resolve import run_resolve_asset
from agent_service.tools.crypto.tokenomics import run_get_tokenomics
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


@function_tool
async def get_crypto_price(
    ctx: RunContextWrapper[ToolDeps], asset: str, vs_currency: str = "usd"
) -> ToolResult[CryptoPriceData]:
    """查询加密资产现价。asset 必须是 resolve_asset 返回的 coin_id，不要传代号。

    Args:
        asset: CoinGecko coin id，例如 "hyperliquid"。
        vs_currency: 计价货币，默认 usd。
    """
    return await run_get_crypto_price(ctx.context, asset=asset, vs_currency=vs_currency)


@function_tool
async def get_market_data(
    ctx: RunContextWrapper[ToolDeps], asset: str, vs_currency: str = "usd"
) -> ToolResult[CryptoMarketData]:
    """查询市值、FDV、供应量、ATH/ATL。asset 必须是 coin_id。

    Args:
        asset: CoinGecko coin id，例如 "hyperliquid"。
        vs_currency: 计价货币，默认 usd。
    """
    return await run_get_market_data(ctx.context, asset=asset, vs_currency=vs_currency)


@function_tool
async def get_price_history(
    ctx: RunContextWrapper[ToolDeps],
    asset: str,
    days: int = 30,
    vs_currency: str = "usd",
) -> ToolResult[CryptoPriceHistoryData]:
    """查询历史价格序列。粒度由 CoinGecko 按 days 自动选择，不要假设固定间隔。

    Args:
        asset: CoinGecko coin id，例如 "hyperliquid"。
        days: 回看天数，1–365，默认 30。
        vs_currency: 计价货币，默认 usd。
    """
    return await run_get_price_history(ctx.context, asset=asset, days=days, vs_currency=vs_currency)


@function_tool
async def get_tokenomics(
    ctx: RunContextWrapper[ToolDeps], asset: str, vs_currency: str = "usd"
) -> ToolResult[TokenomicsData]:
    """查询代币供应量。分配表和解锁日程在免费源上通常没有，看 quality.missing_fields，不要编造。

    asset 必须是 resolve_asset 返回的 coin_id。

    Args:
        asset: CoinGecko coin id，例如 "hyperliquid"。
        vs_currency: 计价货币（影响 FDV），默认 usd。
    """
    return await run_get_tokenomics(ctx.context, asset=asset, vs_currency=vs_currency)


CRYPTO_TOOLS: list[Tool] = [
    resolve_asset,
    get_crypto_price,
    get_market_data,
    get_price_history,
    get_tokenomics,
]
