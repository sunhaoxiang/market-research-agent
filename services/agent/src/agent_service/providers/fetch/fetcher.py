"""任意 URL 抓取 + 正文提取。

不继承 BaseProvider：那边默认跟随重定向、并把响应当 JSON 解析。对
`web_fetch` 来说这两件事都是漏洞——Location 可以跳到 169.254.169.254，
HTML 也不是 JSON。缓存和令牌桶仍然复用 Runtime，HTTP 路径单独走。
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura

from agent_service.providers.cache import cache_key
from agent_service.providers.errors import ProviderError
from agent_service.providers.fetch.ssrf import (
    assert_public_ips,
    blocked,
    hostname_of,
    literal_ips,
    parse_fetch_url,
)
from agent_service.providers.profiles import profile_for
from agent_service.providers.runtime import ProviderRuntime
from agent_service.providers.ttl import CacheTTL
from agent_service.schemas.tools import DataProvenance, ToolErrorCode

type Resolver = Callable[[str], Awaitable[list[str]]]

_USER_AGENT = "market-research-agent/0.0.1 (+https://github.com/sunhaoxiang/market-research-agent)"
_MAX_BYTES = 2 * 1024 * 1024
_MAX_REDIRECTS = 5
_BINARY_PREFIXES = ("image/", "video/", "audio/")
_BINARY_TYPES = frozenset(
    {
        "application/pdf",
        "application/octet-stream",
        "application/zip",
        "application/gzip",
        "application/x-gzip",
        "application/wasm",
    }
)


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str
    final_url: str
    title: str | None
    text: str | None
    status_code: int
    content_type: str | None
    provenance: DataProvenance


@runtime_checkable
class PageFetcher(Protocol):
    """Tool 层只需要 `fetch`。测试替身不必继承 WebFetcher。"""

    async def fetch(self, url: str) -> FetchedPage: ...


class WebFetcher:
    def __init__(
        self,
        runtime: ProviderRuntime,
        *,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver | None = None,
        max_bytes: int = _MAX_BYTES,
        max_redirects: int = _MAX_REDIRECTS,
    ) -> None:
        self.runtime = runtime
        self.name = "web_fetch"
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._resolver = resolver or _default_resolve
        profile = profile_for(self.name)
        self._limiter = runtime.limiter(self.name, profile)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=profile.timeout,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            },
            follow_redirects=False,
            http2=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch(self, url: str) -> FetchedPage:
        requested = parse_fetch_url(url)
        key = cache_key(provider=self.name, method="GET", endpoint=requested)
        cached = await self.runtime.cache.get(key)
        if cached is not None:
            return _page_from_cache(
                requested, cached.body, cached.stored_at_ms, self.runtime.clock.now()
            )

        html, final_url, status, content_type = await self._download(requested)
        title, text = extract_article(html, url=final_url)
        retrieved = self.runtime.clock.now()
        payload = {
            "final_url": final_url,
            "title": title,
            "text": text,
            "status_code": status,
            "content_type": content_type,
        }
        await self.runtime.cache.set(
            key,
            provider=self.name,
            endpoint=requested,
            url=final_url,
            body=payload,
            status_code=status,
            ttl=CacheTTL.WEB_PAGE,
        )
        return FetchedPage(
            url=requested,
            final_url=final_url,
            title=title,
            text=text,
            status_code=status,
            content_type=content_type,
            provenance=DataProvenance(
                provider=self.name,
                endpoint=requested,
                source_url=final_url,
                retrieved_at=retrieved,
                is_cached=False,
                cache_age_s=None,
            ),
        )

    async def _download(self, url: str) -> tuple[str, str, int, str | None]:
        current = url
        for _ in range(self._max_redirects + 1):
            current = parse_fetch_url(current)
            host = hostname_of(current)
            ips = literal_ips(host)
            if ips is None:
                try:
                    ips = await self._resolver(host)
                except OSError as exc:
                    raise ProviderError(
                        ToolErrorCode.NOT_FOUND,
                        "无法解析主机名",
                        retryable=False,
                        provider=self.name,
                    ) from exc
            assert_public_ips(host, ips)
            await self._limiter.acquire()

            try:
                response = await self._client.request("GET", current)
            except httpx.TimeoutException as exc:
                raise ProviderError(
                    ToolErrorCode.TIMEOUT,
                    "抓取超时",
                    retryable=True,
                    provider=self.name,
                    endpoint=current,
                ) from exc
            except httpx.RequestError as exc:
                raise ProviderError(
                    ToolErrorCode.UPSTREAM_ERROR,
                    "抓取失败",
                    retryable=True,
                    provider=self.name,
                    endpoint=current,
                ) from exc

            if response.has_redirect_location:
                location = response.headers.get("Location")
                await response.aclose()
                if not location or not location.strip():
                    raise ProviderError(
                        ToolErrorCode.UPSTREAM_ERROR,
                        "重定向缺少 Location",
                        retryable=False,
                        status_code=response.status_code,
                        provider=self.name,
                        endpoint=current,
                    )
                nxt = urljoin(current, location.strip())
                _reject_https_downgrade(current, nxt)
                current = nxt
                continue

            try:
                return await self._read_body(response, current)
            finally:
                await response.aclose()

        raise blocked(f"重定向超过 {self._max_redirects} 次")

    async def _read_body(
        self, response: httpx.Response, url: str
    ) -> tuple[str, str, int, str | None]:
        if response.status_code == httpx.codes.NOT_FOUND:
            raise ProviderError(
                ToolErrorCode.NOT_FOUND,
                "页面不存在",
                retryable=False,
                status_code=response.status_code,
                provider=self.name,
                endpoint=url,
            )
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise ProviderError(
                ToolErrorCode.UPSTREAM_ERROR,
                f"上游 HTTP {response.status_code}",
                retryable=response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR,
                status_code=response.status_code,
                provider=self.name,
                endpoint=url,
            )

        content_type = response.headers.get("content-type")
        if content_type and _is_binary(content_type):
            raise ProviderError(
                ToolErrorCode.UNSUPPORTED,
                "不支持的内容类型，web_fetch 只提取 HTML 正文",
                retryable=False,
                status_code=response.status_code,
                provider=self.name,
                endpoint=url,
            )

        length = response.headers.get("content-length")
        if length is not None and length.isdigit() and int(length) > self._max_bytes:
            raise blocked(f"响应声明超过 {self._max_bytes} 字节")

        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self._max_bytes:
                raise blocked(f"响应超过 {self._max_bytes} 字节")
            chunks.append(chunk)

        raw = b"".join(chunks)
        charset = _charset(content_type)
        html = raw.decode(charset, errors="replace")
        return html, str(response.url), response.status_code, content_type


def extract_article(html: str, *, url: str) -> tuple[str | None, str | None]:
    text = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    )
    title: str | None = None
    metadata = trafilatura.extract_metadata(html, default_url=url)
    if metadata is not None:
        raw_title = getattr(metadata, "title", None)
        if isinstance(raw_title, str) and raw_title.strip():
            title = raw_title.strip()
    return title, text if text else None


def _is_binary(content_type: str) -> bool:
    mime = content_type.split(";", 1)[0].strip().lower()
    return mime in _BINARY_TYPES or mime.startswith(_BINARY_PREFIXES)


def _charset(content_type: str | None) -> str:
    if not content_type:
        return "utf-8"
    for part in content_type.split(";"):
        piece = part.strip()
        if piece.lower().startswith("charset="):
            value = piece.split("=", 1)[1].strip().strip("\"'")
            return value or "utf-8"
    return "utf-8"


def _reject_https_downgrade(current: str, nxt: str) -> None:
    if urlparse(current).scheme == "https" and urlparse(nxt).scheme == "http":
        raise blocked("拒绝 HTTPS 降级到 HTTP")


def _page_from_cache(requested: str, body: Any, stored_at_ms: int, now: datetime) -> FetchedPage:
    if not isinstance(body, dict):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "缓存中的抓取结果已损坏",
            retryable=False,
            provider="web_fetch",
        )
    age_s = max(int((now.timestamp() * 1000 - stored_at_ms) // 1000), 0)
    final_url = str(body.get("final_url") or requested)
    title = body.get("title")
    text = body.get("text")
    retrieved = datetime.fromtimestamp(stored_at_ms / 1000, tz=UTC)
    ctype = body.get("content_type")
    return FetchedPage(
        url=requested,
        final_url=final_url,
        title=title if isinstance(title, str) else None,
        text=text if isinstance(text, str) else None,
        status_code=int(body.get("status_code") or 200),
        content_type=ctype if isinstance(ctype, str) else None,
        provenance=DataProvenance(
            provider="web_fetch",
            endpoint=requested,
            source_url=final_url,
            retrieved_at=retrieved,
            is_cached=True,
            cache_age_s=age_s,
        ),
    )


async def _default_resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    seen: set[str] = set()
    ips: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in seen:
            seen.add(address)
            ips.append(address)
    return ips
