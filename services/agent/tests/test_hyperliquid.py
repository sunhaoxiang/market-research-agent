"""Hyperliquid provider 契约测试（P3-8 验收）。

钉死三件事：无鉴权、响应映射（成交量/持仓 USD、跳过 delisted、坏 payload）、
以及走 BaseProvider 的缓存路径。Info 查询不要用 K 线去拼历史。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from agent_service.providers.errors import ProviderError
from agent_service.providers.onchain import HyperliquidProvider
from agent_service.providers.runtime import Clock, ProviderRuntime
from agent_service.schemas.tools import ToolErrorCode

_BASE = "https://api.hyperliquid.xyz"
_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_PAGE = "https://app.hyperliquid.xyz"


@pytest.fixture
async def runtime(tmp_path: Path) -> AsyncIterator[ProviderRuntime]:
    clock = Clock.frozen(at=_NOW)

    async def sleep(seconds: float) -> None:
        clock.advance_ms(int(seconds * 1000))

    rt = ProviderRuntime(tmp_path / "cache.db", clock=clock, sleep=sleep)
    await rt.open()
    yield rt
    await rt.aclose()


@asynccontextmanager
async def hyperliquid(runtime: ProviderRuntime) -> AsyncIterator[HyperliquidProvider]:
    async with httpx.AsyncClient(base_url=_BASE) as client:
        yield HyperliquidProvider(runtime=runtime, client=client)


def _market(name: str, *, delisted: bool = False) -> dict[str, object]:
    row: dict[str, object] = {"name": name, "szDecimals": 5, "maxLeverage": 50}
    if delisted:
        row["isDelisted"] = True
    return row


def _ctx(*, day_vlm: str, mark: str, oi: str) -> dict[str, object]:
    return {"dayNtlVlm": day_vlm, "markPx": mark, "openInterest": oi}


def _snapshot(
    universe: list[dict[str, object]] | None = None,
    ctxs: list[dict[str, object]] | None = None,
) -> list[object]:
    if universe is None:
        universe = [_market("AAA"), _market("BBB")]
    if ctxs is None:
        ctxs = [_ctx(day_vlm="100", mark="2", oi="3"), _ctx(day_vlm="50", mark="10", oi="4")]
    return [{"universe": universe}, ctxs]


@respx.mock(base_url=_BASE)
async def test_perp_snapshot_maps_volume_and_open_interest(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.post("/info").mock(return_value=httpx.Response(200, json=_snapshot()))
    async with hyperliquid(runtime) as provider:
        page = await provider.get_perp_snapshot()

    assert page.chain == "Hyperliquid"
    assert page.n_markets == 2
    assert page.volume_24h_usd == pytest.approx(150.0)
    assert page.open_interest_usd == pytest.approx(46.0)
    assert page.url == _PAGE
    assert page.provenance.provider == "hyperliquid"
    assert page.provenance.endpoint == "metaAndAssetCtxs"
    assert page.provenance.source_url == f"{_BASE}/info"


@respx.mock(base_url=_BASE)
async def test_no_auth_header(respx_mock: respx.MockRouter, runtime: ProviderRuntime) -> None:
    route = respx_mock.post("/info").mock(return_value=httpx.Response(200, json=_snapshot()))
    async with hyperliquid(runtime) as provider:
        await provider.get_perp_snapshot()
    sent = json.loads(route.calls[0].request.content.decode())
    assert sent == {"type": "metaAndAssetCtxs"}
    headers = {k.lower(): v for k, v in route.calls[0].request.headers.items()}
    assert "authorization" not in headers
    assert "api_key" not in str(route.calls[0].request.url).lower()
    assert "api_key" not in sent


@respx.mock(base_url=_BASE)
async def test_delisted_markets_are_skipped(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    payload = _snapshot(
        universe=[_market("LIVE"), _market("DEAD", delisted=True)],
        ctxs=[_ctx(day_vlm="100", mark="2", oi="3"), _ctx(day_vlm="999", mark="1", oi="1")],
    )
    respx_mock.post("/info").mock(return_value=httpx.Response(200, json=payload))
    async with hyperliquid(runtime) as provider:
        page = await provider.get_perp_snapshot()
    assert page.n_markets == 1
    assert page.volume_24h_usd == pytest.approx(100.0)
    assert page.open_interest_usd == pytest.approx(6.0)


@respx.mock(base_url=_BASE)
async def test_identical_queries_hit_the_cache(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.post("/info").mock(return_value=httpx.Response(200, json=_snapshot()))
    async with hyperliquid(runtime) as provider:
        first = await provider.get_perp_snapshot()
        second = await provider.get_perp_snapshot()
    assert route.call_count == 1
    assert first.provenance.is_cached is False
    assert second.provenance.is_cached is True


@respx.mock(base_url=_BASE)
async def test_http_400_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.post("/info").mock(return_value=httpx.Response(400, json={"error": "bad"}))
    async with hyperliquid(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_perp_snapshot()
    assert exc.value.code is ToolErrorCode.NOT_FOUND
    assert exc.value.status_code == 400


@respx.mock(base_url=_BASE)
async def test_http_404_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.post("/info").mock(return_value=httpx.Response(404, json={"error": "gone"}))
    async with hyperliquid(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_perp_snapshot()
    assert exc.value.code is ToolErrorCode.NOT_FOUND


@respx.mock(base_url=_BASE)
async def test_malformed_payload_is_parse_error(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.post("/info").mock(return_value=httpx.Response(200, json={"universe": []}))
    async with hyperliquid(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_perp_snapshot()
    assert exc.value.code is ToolErrorCode.PARSE_ERROR


@respx.mock(base_url=_BASE)
async def test_empty_universe_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.post("/info").mock(return_value=httpx.Response(200, json=[{"universe": []}, []]))
    async with hyperliquid(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_perp_snapshot()
    assert exc.value.code is ToolErrorCode.NOT_FOUND


@respx.mock(base_url=_BASE)
async def test_all_delisted_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    payload: list[Any] = [
        {"universe": [_market("DEAD", delisted=True)]},
        [_ctx(day_vlm="1", mark="1", oi="1")],
    ]
    respx_mock.post("/info").mock(return_value=httpx.Response(200, json=payload))
    async with hyperliquid(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_perp_snapshot()
    assert exc.value.code is ToolErrorCode.NOT_FOUND
