"""get_chain_activity。Hyperliquid 包官方 Info 快照；其它链与 DAU/tx 无免费源。"""

from __future__ import annotations

from agent_service.providers.errors import ProviderError
from agent_service.providers.onchain import PerpMarketSnapshot
from agent_service.schemas.tools import DataProvenance, DataQuality, ToolResult
from agent_service.tools._result import (
    fail_invalid,
    fail_provider,
    fail_unavailable,
    fail_unsupported,
)
from agent_service.tools.deps import ToolDeps
from agent_service.tools.onchain.models import ChainActivityData

_TOOL = "get_chain_activity"
_MAX_DAYS = 365
_SNAPSHOT_OPTIONAL = ("n_markets", "volume_24h_usd", "open_interest_usd")
_ALWAYS_MISSING = ("active_addresses", "tx_count")
_COVERAGE = (
    "免费源没有全站活跃地址和交易数时间序列。"
    "本工具只返回 Hyperliquid Info API 的 24h 永续成交量与持仓快照。"
    "DefiLlama active-users、holders、whale、exchange flow 需付费或没有公开 API。"
)
_UNSUPPORTED = (
    "链上活动目前只覆盖 Hyperliquid（官方 Info API 的 24h 成交量与持仓）。"
    "其它链没有接入免费源，不要编造活跃地址或交易数。"
)


async def run_get_chain_activity(
    deps: ToolDeps, *, chain: str, days: int = 1
) -> ToolResult[ChainActivityData]:
    name = chain.strip()
    if not name:
        return fail_invalid(_TOOL, "chain 为空")
    if days < 1 or days > _MAX_DAYS:
        return fail_invalid(_TOOL, f"历史区间必须在 1–{_MAX_DAYS} 天")
    if not _is_hyperliquid(name):
        return fail_unsupported(_TOOL, _UNSUPPORTED, provider="hyperliquid")
    if deps.hyperliquid is None:
        return fail_unavailable(tool=_TOOL, provider="hyperliquid", message="Hyperliquid 未初始化")
    try:
        page = await deps.hyperliquid.get_perp_snapshot()
    except ProviderError as exc:
        return fail_provider(_TOOL, exc)
    data = _activity(page, days=days)
    caveats = [_COVERAGE]
    if days != 1:
        caveats.append("Hyperliquid Info API 只有 24h 快照，days 未应用，不要当成历史序列。")
    return ToolResult.success(
        data,
        _page_provenance(page.provenance, page.url),
        quality=_quality(data, caveats=caveats),
    )


def _activity(page: PerpMarketSnapshot, *, days: int) -> ChainActivityData:
    return ChainActivityData(
        chain=page.chain,
        days=days,
        n_markets=page.n_markets,
        volume_24h_usd=page.volume_24h_usd,
        open_interest_usd=page.open_interest_usd,
        url=page.url,
    )


def _is_hyperliquid(chain: str) -> bool:
    key = "".join(ch for ch in chain.lower() if ch.isalnum())
    return key in {"hyperliquid", "hyperliquidl1"}


def _quality(data: ChainActivityData, *, caveats: list[str]) -> DataQuality:
    missing = [name for name in _SNAPSHOT_OPTIONAL if getattr(data, name) is None]
    missing.extend(name for name in _ALWAYS_MISSING if name not in missing)
    return DataQuality(completeness="partial", missing_fields=missing, caveats=caveats)


def _page_provenance(provenance: DataProvenance, url: str) -> DataProvenance:
    return provenance.model_copy(update={"source_url": url, "as_of": provenance.retrieved_at})
