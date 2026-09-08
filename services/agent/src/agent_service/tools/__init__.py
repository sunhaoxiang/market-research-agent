"""面向 Agent 的 tool。实现按域拆分；HTTP invoke 走 `registry.invoke_tool`。"""

from agent_service.tools.crypto.bindings import (
    CRYPTO_TOOLS,
    get_crypto_price,
    get_market_data,
    get_price_history,
    resolve_asset,
)
from agent_service.tools.defi.bindings import (
    DEFI_TOOLS,
    get_chain_overview,
    get_dex_volume,
    get_protocol_fees_revenue,
    get_tvl,
)
from agent_service.tools.deps import ToolDeps
from agent_service.tools.registry import HANDLERS, invoke_tool
from agent_service.tools.system.bindings import SYSTEM_TOOLS, compute_metrics
from agent_service.tools.web.bindings import WEB_TOOLS, news_search, web_fetch, web_search

__all__ = [
    "CRYPTO_TOOLS",
    "DEFI_TOOLS",
    "HANDLERS",
    "SYSTEM_TOOLS",
    "WEB_TOOLS",
    "ToolDeps",
    "compute_metrics",
    "get_chain_overview",
    "get_crypto_price",
    "get_dex_volume",
    "get_market_data",
    "get_price_history",
    "get_protocol_fees_revenue",
    "get_tvl",
    "invoke_tool",
    "news_search",
    "resolve_asset",
    "web_fetch",
    "web_search",
]
