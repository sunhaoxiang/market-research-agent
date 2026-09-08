"""没有免费源的链上 tool。返回 UNSUPPORTED，让 Agent 写入 data_gaps 而不是编数字。"""

from __future__ import annotations

from agent_service.schemas.tools import ToolResult
from agent_service.tools._result import fail_invalid, fail_unsupported
from agent_service.tools.deps import ToolDeps

_HOLDERS = (
    "代币持有人分布没有免费 API（Nansen / Arkham / Dune 需付费）。"
    "不要编造持仓集中度；把缺口写入 data_gaps。"
)
_WHALE = (
    "巨鲸转账没有免费 API（Nansen / Arkham / Whale Alert 需付费或不可靠）。"
    "不要编造大额流向；把缺口写入 data_gaps。"
)
_FLOW = (
    "交易所净流入/流出没有免费 API（CryptoQuant / Glassnode 需付费）。"
    "不要编造资金进出；把缺口写入 data_gaps。"
)


async def run_get_token_holders(deps: ToolDeps, *, asset: str) -> ToolResult[None]:
    del deps
    if not asset.strip():
        return fail_invalid("get_token_holders", "asset 为空")
    return fail_unsupported("get_token_holders", _HOLDERS)


async def run_get_whale_activity(
    deps: ToolDeps, *, asset: str, threshold: float | None = None
) -> ToolResult[None]:
    del deps, threshold
    if not asset.strip():
        return fail_invalid("get_whale_activity", "asset 为空")
    return fail_unsupported("get_whale_activity", _WHALE)


async def run_get_exchange_flow(deps: ToolDeps, *, asset: str, days: int = 30) -> ToolResult[None]:
    del deps, days
    if not asset.strip():
        return fail_invalid("get_exchange_flow", "asset 为空")
    return fail_unsupported("get_exchange_flow", _FLOW)
