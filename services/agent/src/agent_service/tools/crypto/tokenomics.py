"""get_tokenomics。供应量包 CoinGecko `get_market`；分配/解锁无免费 API。"""

from __future__ import annotations

import math
from datetime import datetime

from agent_service.providers.crypto import CoinMarket
from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import DataProvenance, DataQuality, ToolResult
from agent_service.tools._result import fail_invalid, fail_provider, fail_unavailable
from agent_service.tools.crypto.models import TokenomicsData
from agent_service.tools.deps import ToolDeps

_TOOL = "get_tokenomics"
_SUPPLY_OPTIONAL = (
    "circulating_supply",
    "total_supply",
    "max_supply",
    "fully_diluted_valuation",
    "circulating_pct",
)
_STRUCTURE = ("allocations", "unlocks")
_COVERAGE_CAVEAT = (
    "CoinGecko Demo API 只有供应量，没有分配表和解锁日程。"
    "网站 Tokenomics 页来自 Tokenomist，未进 API。"
    "DefiLlama emissions 需 Pro，本项不用。"
)


async def run_get_tokenomics(
    deps: ToolDeps, *, asset: str, vs_currency: str = "usd"
) -> ToolResult[TokenomicsData]:
    coin_id = asset.strip()
    if not coin_id:
        return fail_invalid(_TOOL, "asset 为空，请先用 resolve_asset 拿到 coin_id")
    if deps.coingecko is None:
        return fail_unavailable(tool=_TOOL, provider="coingecko", message="CoinGecko 未初始化")
    try:
        row = await deps.coingecko.get_market(coin_id, vs_currency=vs_currency)
    except ProviderError as exc:
        return fail_provider(_TOOL, exc)
    data = _tokenomics(row)
    return ToolResult.success(
        data,
        _page_provenance(row.provenance, row.url, as_of=row.last_updated),
        quality=_quality(data),
    )


def _tokenomics(row: CoinMarket) -> TokenomicsData:
    return TokenomicsData(
        coin_id=row.coin_id,
        symbol=row.symbol,
        name=row.name,
        vs_currency=row.vs_currency,
        circulating_supply=row.circulating_supply,
        total_supply=row.total_supply,
        max_supply=row.max_supply,
        fully_diluted_valuation=row.fully_diluted_valuation,
        circulating_pct=_circulating_pct(row.circulating_supply, row.max_supply),
        url=row.url,
    )


def _circulating_pct(circulating: float | None, max_supply: float | None) -> float | None:
    if circulating is None or max_supply is None:
        return None
    if not math.isfinite(circulating) or not math.isfinite(max_supply) or max_supply <= 0:
        return None
    return circulating / max_supply


def _quality(data: TokenomicsData) -> DataQuality:
    missing = [name for name in _SUPPLY_OPTIONAL if getattr(data, name) is None]
    missing.extend(name for name in _STRUCTURE if not getattr(data, name) and name not in missing)
    return DataQuality(completeness="partial", missing_fields=missing, caveats=[_COVERAGE_CAVEAT])


def _page_provenance(
    provenance: DataProvenance, url: str, *, as_of: datetime | None = None
) -> DataProvenance:
    return provenance.model_copy(update={"source_url": url, "as_of": as_of})
