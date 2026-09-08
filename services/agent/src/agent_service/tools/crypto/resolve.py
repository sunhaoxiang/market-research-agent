"""resolve_asset：符号 → coin id。底层只调 `search_coins`，不再包 HTTP。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_service.providers.crypto import CoinSearchHit
from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import DataQuality, ToolError, ToolErrorCode, ToolResult
from agent_service.tools._result import fail_invalid, fail_provider, fail_unavailable
from agent_service.tools.crypto.models import AssetMatchKind, ResolveAssetData, ResolvedAsset
from agent_service.tools.deps import ToolDeps

_TOOL = "resolve_asset"
_MAX_CANDIDATES = 8


@dataclass(frozen=True, slots=True)
class CoinResolution:
    resolved: CoinSearchHit | None
    candidates: tuple[CoinSearchHit, ...]
    kind: AssetMatchKind


def disambiguate_coins(query: str, hits: tuple[CoinSearchHit, ...]) -> CoinResolution:
    """纯函数：同符号冲突按市值排名取唯一最优，并列则交给 Agent 选。"""
    fold = query.strip().casefold()
    id_matches = tuple(hit for hit in hits if hit.id.casefold() == fold)
    if len(id_matches) == 1:
        return _picked(id_matches[0], id_matches, AssetMatchKind.EXACT_ID)

    by_symbol = _decide_pool(
        tuple(hit for hit in hits if hit.symbol.casefold() == fold),
        exact=AssetMatchKind.EXACT_SYMBOL,
        ranked=AssetMatchKind.RANKED_SYMBOL,
    )
    if by_symbol is not None:
        return by_symbol

    by_name = _decide_pool(
        tuple(hit for hit in hits if hit.name.casefold() == fold),
        exact=AssetMatchKind.EXACT_NAME,
        ranked=AssetMatchKind.EXACT_NAME,
    )
    if by_name is not None:
        return by_name

    if len(hits) == 1:
        return _picked(hits[0], hits, AssetMatchKind.UNIQUE_HIT)
    return CoinResolution(
        resolved=None,
        candidates=_cap(None, hits),
        kind=AssetMatchKind.AMBIGUOUS,
    )


def _decide_pool(
    pool: tuple[CoinSearchHit, ...],
    *,
    exact: AssetMatchKind,
    ranked: AssetMatchKind,
) -> CoinResolution | None:
    if not pool:
        return None
    if len(pool) == 1:
        return _picked(pool[0], pool, exact)
    winner = _unique_best_rank(pool)
    if winner is not None:
        return _picked(winner, pool, ranked)
    return CoinResolution(
        resolved=None,
        candidates=_cap(None, pool),
        kind=AssetMatchKind.AMBIGUOUS,
    )


async def run_resolve_asset(deps: ToolDeps, *, query: str) -> ToolResult[ResolveAssetData]:
    q = query.strip()
    if not q:
        return fail_invalid(_TOOL, "查询词为空")
    if deps.coingecko is None:
        return fail_unavailable(
            tool=_TOOL,
            provider="coingecko",
            message="CoinGecko 未初始化",
        )
    try:
        page = await deps.coingecko.search_coins(q)
    except ProviderError as exc:
        return fail_provider(_TOOL, exc)
    if not page.hits:
        return ToolResult.failure(
            ToolError(
                code=ToolErrorCode.NOT_FOUND,
                message=f"没有匹配的加密资产：{q}",
                tool=_TOOL,
                provider="coingecko",
                retryable=False,
            )
        )

    decision = disambiguate_coins(q, page.hits)
    resolved = None if decision.resolved is None else _asset(decision.resolved)
    candidates = [_asset(hit) for hit in decision.candidates]
    provenance = page.provenance
    if resolved is not None:
        provenance = provenance.model_copy(update={"source_url": resolved.url})
    return ToolResult.success(
        ResolveAssetData(
            query=q,
            match=decision.kind,
            resolved=resolved,
            candidates=candidates,
        ),
        provenance,
        quality=_quality(decision),
    )


def _picked(
    winner: CoinSearchHit, pool: tuple[CoinSearchHit, ...], kind: AssetMatchKind
) -> CoinResolution:
    return CoinResolution(resolved=winner, candidates=_cap(winner, pool), kind=kind)


def _unique_best_rank(hits: tuple[CoinSearchHit, ...]) -> CoinSearchHit | None:
    ranked = [hit for hit in hits if hit.market_cap_rank is not None]
    if not ranked:
        return None
    ranked.sort(key=lambda hit: (hit.market_cap_rank or 0, hit.id))
    best = ranked[0]
    tied = any(hit.id != best.id and hit.market_cap_rank == best.market_cap_rank for hit in ranked)
    if tied:
        return None
    return best


def _cap(
    resolved: CoinSearchHit | None, hits: tuple[CoinSearchHit, ...]
) -> tuple[CoinSearchHit, ...]:
    ordered = _by_rank(hits)
    if resolved is not None:
        rest = tuple(hit for hit in ordered if hit.id != resolved.id)
        ordered = (resolved, *rest)
    return ordered[:_MAX_CANDIDATES]


def _by_rank(hits: tuple[CoinSearchHit, ...]) -> tuple[CoinSearchHit, ...]:
    return tuple(
        sorted(
            hits,
            key=lambda hit: (hit.market_cap_rank is None, hit.market_cap_rank or 0, hit.id),
        )
    )


def _asset(hit: CoinSearchHit) -> ResolvedAsset:
    return ResolvedAsset(
        coin_id=hit.id,
        symbol=hit.symbol,
        name=hit.name,
        market_cap_rank=hit.market_cap_rank,
        url=hit.url,
    )


def _quality(decision: CoinResolution) -> DataQuality | None:
    extras = len(decision.candidates) - (0 if decision.resolved is None else 1)
    if decision.kind is AssetMatchKind.AMBIGUOUS:
        return DataQuality(
            completeness="partial",
            missing_fields=["resolved"],
            caveats=["多个候选无法唯一确定，请用返回的 coin_id 再调 resolve_asset"],
        )
    if extras > 0:
        return DataQuality(
            completeness="full",
            caveats=[f"还有 {extras} 个其他候选，已按市值排名选取。后续请用 coin_id"],
        )
    return None
