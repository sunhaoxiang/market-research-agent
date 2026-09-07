"""BaseProvider 契约：429 / 5xx / 超时 / 缓存命中（P2-1 验收）。

这些路径一旦静默出错，表现全是"偶尔贵、偶尔慢、偶尔 429"，
日志里看不出是重试没发生还是缓存没写上。所以每条都用 respx 钉死。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import respx

from agent_service.providers.base import BaseProvider
from agent_service.providers.errors import ProviderError
from agent_service.providers.profiles import ProviderProfile, RetryPolicy
from agent_service.providers.runtime import Clock, ProviderRuntime
from agent_service.providers.ttl import CacheTTL
from agent_service.schemas.tools import ToolErrorCode

_BASE = "https://api.example.test"
_FAST_RETRY = RetryPolicy(attempts=3, initial_wait_s=0.0, max_wait_s=0.0)


@pytest.fixture
async def runtime(tmp_path: Path) -> AsyncIterator[ProviderRuntime]:
    clock = Clock.frozen()
    rt = ProviderRuntime(tmp_path / "cache.db", clock=clock, sleep=_instant_sleep(clock))
    await rt.open()
    yield rt
    await rt.aclose()


def _instant_sleep(clock: Clock):
    async def sleep(seconds: float) -> None:
        clock.advance_ms(int(seconds * 1000))

    return sleep


def _profile(**overrides: object) -> ProviderProfile:
    values: dict[str, object] = {
        "rate_per_second": None,
        "burst": 5,
        "retry": _FAST_RETRY,
    }
    values.update(overrides)
    return ProviderProfile(**values)  # type: ignore[arg-type]


@asynccontextmanager
async def dummy(runtime: ProviderRuntime, **overrides: object) -> AsyncIterator[BaseProvider]:
    async with httpx.AsyncClient(base_url=_BASE) as client:
        yield BaseProvider(
            name="dummy",
            base_url=_BASE,
            runtime=runtime,
            client=client,
            profile=_profile(**overrides),
        )


@respx.mock(base_url=_BASE)
async def test_second_call_is_served_from_cache(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/price").mock(return_value=httpx.Response(200, json={"p": 1.25}))
    async with dummy(runtime) as provider:
        first = await provider.get_json("/price", ttl=CacheTTL.REALTIME)
        second = await provider.get_json("/price", ttl=CacheTTL.REALTIME)
        stats = provider.stats

    assert route.call_count == 1
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.data == {"p": 1.25}
    assert second.cache_age_s == 0
    assert second.provenance().is_cached is True
    assert stats.cache_hits == 1
    assert stats.http_attempts == 1


@respx.mock(base_url=_BASE)
async def test_expired_cache_refetches(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/price").mock(return_value=httpx.Response(200, json={"p": 1}))
    async with dummy(runtime) as provider:
        await provider.get_json("/price", ttl=CacheTTL.REALTIME)
        runtime.clock.advance_ms(61_000)
        again = await provider.get_json("/price", ttl=CacheTTL.REALTIME)
        attempts = provider.stats.http_attempts

    assert again.from_cache is False
    assert attempts == 2


@respx.mock(base_url=_BASE)
async def test_cache_key_does_not_include_authorization(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/x").mock(return_value=httpx.Response(200, json={"ok": True}))
    async with dummy(runtime) as provider:
        await provider.get_json("/x", ttl=CacheTTL.MARKET, headers={"Authorization": "k1"})
        await provider.get_json("/x", ttl=CacheTTL.MARKET, headers={"Authorization": "k2"})
    assert route.call_count == 1


@respx.mock(base_url=_BASE)
async def test_429_is_retried_then_succeeds(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/x").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    async with dummy(runtime) as provider:
        result = await provider.get_json("/x", ttl=CacheTTL.MARKET)

    assert result.data == {"ok": True}
    assert result.attempts == 2
    assert result.from_cache is False


@respx.mock(base_url=_BASE)
async def test_429_exhausted_becomes_rate_limited(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/x").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "0"})
    )
    async with dummy(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_json("/x", ttl=CacheTTL.MARKET)

    assert exc.value.code is ToolErrorCode.RATE_LIMITED
    assert route.call_count == 3


@respx.mock(base_url=_BASE)
async def test_5xx_is_retried_then_succeeds(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/x").mock(
        side_effect=[
            httpx.Response(503, json={"err": "busy"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    async with dummy(runtime) as provider:
        result = await provider.get_json("/x", ttl=CacheTTL.MARKET)
    assert result.data == {"ok": True}
    assert result.attempts == 2


@respx.mock(base_url=_BASE)
async def test_5xx_exhausted_is_upstream_error(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/x").mock(return_value=httpx.Response(502))
    async with dummy(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_json("/x", ttl=CacheTTL.MARKET)
    assert exc.value.code is ToolErrorCode.UPSTREAM_ERROR
    assert exc.value.status_code == 502


@respx.mock(base_url=_BASE)
async def test_timeout_is_retried_then_succeeds(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/x").mock(
        side_effect=[
            httpx.ReadTimeout("read timed out"),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    async with dummy(runtime) as provider:
        result = await provider.get_json("/x", ttl=CacheTTL.MARKET)
    assert result.data == {"ok": True}


@respx.mock(base_url=_BASE)
async def test_timeout_exhausted_is_timeout(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/x").mock(side_effect=httpx.ReadTimeout("read timed out"))
    async with dummy(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_json("/x", ttl=CacheTTL.MARKET)
    assert exc.value.code is ToolErrorCode.TIMEOUT


@respx.mock(base_url=_BASE)
async def test_4xx_is_not_retried(respx_mock: respx.MockRouter, runtime: ProviderRuntime) -> None:
    route = respx_mock.get("/x").mock(return_value=httpx.Response(404, json={"err": "missing"}))
    async with dummy(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_json("/x", ttl=CacheTTL.MARKET)
    assert exc.value.status_code == 404
    assert exc.value.retryable is False
    assert route.call_count == 1


@respx.mock(base_url=_BASE)
async def test_quota_exhausted_fails_fast_without_http(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/x").mock(return_value=httpx.Response(200, json={"ok": True}))
    async with dummy(runtime, daily_quota=1) as provider:
        await provider.get_json("/x", ttl=CacheTTL.MARKET)
        with pytest.raises(ProviderError) as exc:
            await provider.get_json("/y", ttl=CacheTTL.MARKET)
    assert exc.value.code is ToolErrorCode.QUOTA_EXHAUSTED
    assert route.call_count == 1


@respx.mock(base_url=_BASE)
async def test_cache_hit_does_not_consume_quota(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/x").mock(return_value=httpx.Response(200, json={"ok": True}))
    async with dummy(runtime, daily_quota=1) as provider:
        await provider.get_json("/x", ttl=CacheTTL.MARKET)
        hit = await provider.get_json("/x", ttl=CacheTTL.MARKET)
    assert hit.from_cache is True
    snap = await runtime.quota.snapshot("dummy", daily_limit=1, monthly_limit=None)
    assert snap.daily_used == 1


@respx.mock(base_url=_BASE)
async def test_non_json_200_is_parse_error(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/x").mock(return_value=httpx.Response(200, text="<html>nope</html>"))
    async with dummy(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_json("/x", ttl=CacheTTL.MARKET)
    assert exc.value.code is ToolErrorCode.PARSE_ERROR
