"""Provider 进程内统计 + 配额（§16.2 / §20.2，P6-9）。

不落库：重启归零可接受。配额窗口本身在 Provider SQLite 里，这里只是读快照。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from agent_service.api.auth import require_internal_token
from agent_service.providers.base import ProviderStats
from agent_service.providers.profiles import profile_for

router = APIRouter(prefix="/v1/debug", tags=["debug"])

# app.state 属性名。顺序与 /v1/health 的 data_sources 对齐，外加 web_fetch。
_PROVIDER_STATE: tuple[tuple[str, str], ...] = (
    ("tavily", "search_provider"),
    ("web_fetch", "web_fetcher"),
    ("coingecko", "coingecko"),
    ("defillama", "defillama"),
    ("hyperliquid", "hyperliquid"),
    ("fmp", "fmp"),
    ("sec_edgar", "sec_edgar"),
)


class ProviderDebugRow(BaseModel):
    provider: str
    configured: bool
    requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cache_hit_rate: float | None = None
    http_attempts: int = 0
    errors: int = 0
    daily_used: int | None = None
    daily_limit: int | None = None
    daily_remaining: int | None = None
    monthly_used: int | None = None
    monthly_limit: int | None = None
    monthly_remaining: int | None = None


class ProvidersDebugResponse(BaseModel):
    providers: list[ProviderDebugRow] = Field(default_factory=list)


@router.get(
    "/providers",
    response_model=ProvidersDebugResponse,
    dependencies=[Depends(require_internal_token)],
)
async def list_providers(request: Request) -> ProvidersDebugResponse:
    runtime = getattr(request.app.state, "provider_runtime", None)
    rows: list[ProviderDebugRow] = []
    for name, attr in _PROVIDER_STATE:
        instance = getattr(request.app.state, attr, None)
        stats = _stats(instance)
        profile = profile_for(name)
        hit_rate = (stats.cache_hits / stats.requests) if stats.requests else None
        daily_used = daily_limit = daily_remaining = None
        monthly_used = monthly_limit = monthly_remaining = None
        if runtime is not None:
            try:
                snap = await runtime.quota.snapshot(
                    name,
                    daily_limit=profile.daily_quota,
                    monthly_limit=profile.monthly_quota,
                )
            except RuntimeError:
                snap = None
            if snap is not None:
                daily_used = snap.daily_used
                daily_limit = snap.daily_limit
                daily_remaining = snap.daily_remaining
                monthly_used = snap.monthly_used
                monthly_limit = snap.monthly_limit
                monthly_remaining = snap.monthly_remaining
        rows.append(
            ProviderDebugRow(
                provider=name,
                configured=instance is not None,
                requests=stats.requests,
                cache_hits=stats.cache_hits,
                cache_misses=stats.cache_misses,
                cache_hit_rate=hit_rate,
                http_attempts=stats.http_attempts,
                errors=stats.errors,
                daily_used=daily_used,
                daily_limit=daily_limit,
                daily_remaining=daily_remaining,
                monthly_used=monthly_used,
                monthly_limit=monthly_limit,
                monthly_remaining=monthly_remaining,
            )
        )
    return ProvidersDebugResponse(providers=rows)


def _stats(instance: object) -> ProviderStats:
    stats = getattr(instance, "stats", None)
    if isinstance(stats, ProviderStats):
        return stats
    return ProviderStats()
