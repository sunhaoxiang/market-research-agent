"""Hyperliquid Info API 客户端（P3-8）。

无需 key。只取 `metaAndAssetCtxs` 快照：24h 名义成交量与持仓（USD）。
没有全站活跃地址 / 交易数时间序列，tool 层不要用 K 线去拼。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import httpx

from agent_service.providers.base import BaseProvider, ProviderResponse
from agent_service.providers.errors import ProviderError
from agent_service.providers.runtime import ProviderRuntime
from agent_service.providers.ttl import CacheTTL
from agent_service.schemas.tools import DataProvenance, ToolErrorCode

_HL_BASE = "https://api.hyperliquid.xyz"
_INFO_PATH = "/info"
_SNAPSHOT = "metaAndAssetCtxs"
_PAGE = "https://app.hyperliquid.xyz"


def stats_page_url() -> str:
    """给人点的页面，不是 API URL。"""
    return _PAGE


@dataclass(frozen=True, slots=True)
class PerpMarketSnapshot:
    chain: str
    n_markets: int
    volume_24h_usd: float | None
    open_interest_usd: float | None
    url: str
    provenance: DataProvenance


class HyperliquidProvider(BaseProvider):
    def __init__(
        self,
        *,
        runtime: ProviderRuntime,
        client: httpx.AsyncClient | None = None,
        base_url: str = _HL_BASE,
    ) -> None:
        super().__init__(
            name="hyperliquid",
            base_url=base_url,
            runtime=runtime,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            client=client,
        )

    async def get_perp_snapshot(self) -> PerpMarketSnapshot:
        try:
            response = await self.post_json(
                _INFO_PATH,
                ttl=CacheTTL.MARKET,
                json_body={"type": _SNAPSHOT},
            )
        except ProviderError as exc:
            raise _map_http_error(exc) from exc
        return _parse_snapshot(response)


def _map_http_error(exc: ProviderError) -> ProviderError:
    if exc.status_code in {httpx.codes.BAD_REQUEST, httpx.codes.NOT_FOUND}:
        return ProviderError(
            ToolErrorCode.NOT_FOUND,
            "Hyperliquid 没有这项数据",
            retryable=False,
            status_code=exc.status_code,
            provider="hyperliquid",
            endpoint=_SNAPSHOT,
        )
    return exc


def _parse_snapshot(response: ProviderResponse) -> PerpMarketSnapshot:
    match response.data:
        case [dict() as meta, list() as ctxs, *_]:
            universe = meta.get("universe")
        case _:
            raise ProviderError(
                ToolErrorCode.PARSE_ERROR,
                "Hyperliquid 快照形状异常",
                provider="hyperliquid",
                endpoint=_SNAPSHOT,
            )
    if not isinstance(universe, list) or not universe:
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            "Hyperliquid 没有永续市场",
            provider="hyperliquid",
            endpoint=_SNAPSHOT,
        )
    volume = 0.0
    open_interest = 0.0
    n_markets = 0
    saw_volume = False
    saw_oi = False
    for market, ctx in zip(universe, ctxs, strict=False):
        if not isinstance(market, dict) or not isinstance(ctx, dict):
            continue
        if market.get("isDelisted") is True:
            continue
        n_markets += 1
        day_vlm = _as_float(ctx.get("dayNtlVlm"))
        if day_vlm is not None:
            volume += day_vlm
            saw_volume = True
        mark = _as_float(ctx.get("markPx"))
        size = _as_float(ctx.get("openInterest"))
        if mark is not None and size is not None:
            open_interest += mark * size
            saw_oi = True
    if n_markets == 0:
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            "Hyperliquid 没有永续市场",
            provider="hyperliquid",
            endpoint=_SNAPSHOT,
        )
    return PerpMarketSnapshot(
        chain="Hyperliquid",
        n_markets=n_markets,
        volume_24h_usd=volume if saw_volume else None,
        open_interest_usd=open_interest if saw_oi else None,
        url=stats_page_url(),
        provenance=response.provenance().model_copy(update={"endpoint": _SNAPSHOT}),
    )


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number
