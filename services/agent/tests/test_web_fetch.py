"""web_fetch 契约：内网 IP 与超大响应必须在发出危险请求之前拦住。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx

from agent_service.providers.errors import ProviderError
from agent_service.providers.fetch import WebFetcher
from agent_service.providers.runtime import Clock, ProviderRuntime
from agent_service.schemas.tools import ToolErrorCode

_PUBLIC = "93.184.216.34"
_HTML = """<!DOCTYPE html>
<html lang="en">
<head><title>HYPE token overview</title></head>
<body>
<article>
<h1>HYPE token overview</h1>
<p>Hyperliquid's native token HYPE is used for gas and governance on the L1.</p>
<p>The protocol collects trading fees from perpetual futures and shares them with stakers.</p>
<p>This paragraph exists so trafilatura treats the page as an article, not chrome.</p>
</article>
</body>
</html>
"""


@pytest.fixture
async def runtime(tmp_path: Path) -> AsyncIterator[ProviderRuntime]:
    clock = Clock.frozen()

    async def sleep(seconds: float) -> None:
        clock.advance_ms(int(seconds * 1000))

    rt = ProviderRuntime(tmp_path / "cache.db", clock=clock, sleep=sleep)
    await rt.open()
    yield rt
    await rt.aclose()


async def _public_dns(host: str) -> list[str]:
    if host in {"127.0.0.1", "localhost"}:
        return ["127.0.0.1"]
    if host == "evil.test":
        return ["127.0.0.1"]
    return [_PUBLIC]


async def _fetcher(runtime: ProviderRuntime, *, max_bytes: int = 2_000_000) -> WebFetcher:
    client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
    return WebFetcher(
        runtime,
        client=client,
        resolver=_public_dns,
        max_bytes=max_bytes,
        max_redirects=3,
    )


@respx.mock
async def test_extracts_article_text(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("https://example.test/hype").mock(
        return_value=httpx.Response(200, text=_HTML, headers={"content-type": "text/html"})
    )
    fetcher = await _fetcher(runtime)
    page = await fetcher.fetch("https://example.test/hype")
    await fetcher._client.aclose()

    assert page.status_code == 200
    assert page.provenance.provider == "web_fetch"
    assert page.text is not None
    assert "HYPE" in page.text
    assert page.title is not None


@respx.mock
async def test_loopback_never_hits_the_wire(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get(url__regex=r"http://127\.0\.0\.1/.*").mock(
        return_value=httpx.Response(200, text="secret")
    )
    fetcher = await _fetcher(runtime)
    with pytest.raises(ProviderError) as exc:
        await fetcher.fetch("http://127.0.0.1:8000/v1/health")
    await fetcher._client.aclose()
    assert exc.value.code is ToolErrorCode.BLOCKED
    assert route.call_count == 0


@respx.mock
async def test_dns_to_loopback_never_hits_the_wire(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("https://evil.test/").mock(
        return_value=httpx.Response(200, text="secret")
    )
    fetcher = await _fetcher(runtime)
    with pytest.raises(ProviderError) as exc:
        await fetcher.fetch("https://evil.test/")
    await fetcher._client.aclose()
    assert exc.value.code is ToolErrorCode.BLOCKED
    assert route.call_count == 0


@respx.mock
async def test_redirect_to_loopback_is_blocked(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("https://example.test/go").mock(
        return_value=httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
    )
    internal = respx_mock.get("http://127.0.0.1/secret").mock(
        return_value=httpx.Response(200, text="secret")
    )
    fetcher = await _fetcher(runtime)
    with pytest.raises(ProviderError) as exc:
        await fetcher.fetch("https://example.test/go")
    await fetcher._client.aclose()
    assert exc.value.code is ToolErrorCode.BLOCKED
    assert internal.call_count == 0


@respx.mock
async def test_https_downgrade_is_blocked(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("https://example.test/go").mock(
        return_value=httpx.Response(302, headers={"Location": "http://example.test/ok"})
    )
    http_hop = respx_mock.get("http://example.test/ok").mock(
        return_value=httpx.Response(200, text=_HTML)
    )
    fetcher = await _fetcher(runtime)
    with pytest.raises(ProviderError) as exc:
        await fetcher.fetch("https://example.test/go")
    await fetcher._client.aclose()
    assert exc.value.code is ToolErrorCode.BLOCKED
    assert http_hop.call_count == 0


@respx.mock
async def test_content_length_over_limit_is_blocked(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("https://example.test/big").mock(
        return_value=httpx.Response(
            200,
            headers={"Content-Length": "9999999", "content-type": "text/html"},
            content=b"<html>tiny</html>",
        )
    )
    fetcher = await _fetcher(runtime, max_bytes=1024)
    with pytest.raises(ProviderError) as exc:
        await fetcher.fetch("https://example.test/big")
    await fetcher._client.aclose()
    assert exc.value.code is ToolErrorCode.BLOCKED
    assert "字节" in exc.value.message


@respx.mock
async def test_streaming_body_over_limit_is_blocked(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("https://example.test/stream").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html>" + b"x" * 500 + b"</html>",
        )
    )
    fetcher = await _fetcher(runtime, max_bytes=64)
    with pytest.raises(ProviderError) as exc:
        await fetcher.fetch("https://example.test/stream")
    await fetcher._client.aclose()
    assert exc.value.code is ToolErrorCode.BLOCKED


@respx.mock
async def test_second_fetch_is_served_from_cache(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("https://example.test/hype").mock(
        return_value=httpx.Response(200, text=_HTML, headers={"content-type": "text/html"})
    )
    fetcher = await _fetcher(runtime)
    first = await fetcher.fetch("https://example.test/hype")
    second = await fetcher.fetch("https://example.test/hype")
    await fetcher._client.aclose()
    assert route.call_count == 1
    assert first.provenance.is_cached is False
    assert second.provenance.is_cached is True
    assert second.text == first.text


@respx.mock
async def test_pdf_is_unsupported(respx_mock: respx.MockRouter, runtime: ProviderRuntime) -> None:
    respx_mock.get("https://example.test/a.pdf").mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"}
        )
    )
    fetcher = await _fetcher(runtime)
    with pytest.raises(ProviderError) as exc:
        await fetcher.fetch("https://example.test/a.pdf")
    await fetcher._client.aclose()
    assert exc.value.code is ToolErrorCode.UNSUPPORTED


async def test_dns_timeout_does_not_hang(runtime: ProviderRuntime) -> None:
    async def slow(_host: str) -> list[str]:
        await asyncio.sleep(30)
        return [_PUBLIC]

    client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
    fetcher = WebFetcher(runtime, client=client, resolver=slow, dns_timeout_s=0.05)
    started = time.monotonic()
    with pytest.raises(ProviderError) as exc:
        await fetcher.fetch("https://example.test/hype")
    elapsed = time.monotonic() - started
    await fetcher._client.aclose()
    assert exc.value.code is ToolErrorCode.TIMEOUT
    assert "DNS" in exc.value.message
    assert elapsed < 2
