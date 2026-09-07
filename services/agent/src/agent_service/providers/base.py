"""所有外部 API 客户端的基类（§8.3）。

一次 `request()` 的顺序：

    缓存 → 配额预检 → [令牌桶 → 扣配额 → HTTP] × 重试 → 写入缓存

缓存命中是唯一一条不发出网络请求的路径，因此它既不排队也不记账。
重试只覆盖 429 / 5xx / 超时 / 网络错误；4xx（含 404）立刻失败——
对"标的不存在"重试既浪费配额，也会把瞬时问题伪装成慢。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception_type, stop_after_attempt

from agent_service.observability.logging import get_logger
from agent_service.providers.cache import cache_key
from agent_service.providers.errors import ProviderError, RetryableProviderError
from agent_service.providers.profiles import ProviderProfile, RetryPolicy, profile_for
from agent_service.providers.runtime import ProviderRuntime
from agent_service.providers.ttl import CacheTTL
from agent_service.schemas.tools import DataProvenance, ToolErrorCode

log = get_logger(__name__)

_RETRYABLE_STATUS = frozenset(
    {
        httpx.codes.REQUEST_TIMEOUT,  # 408
        httpx.codes.TOO_MANY_REQUESTS,  # 429
        httpx.codes.INTERNAL_SERVER_ERROR,
        httpx.codes.BAD_GATEWAY,
        httpx.codes.SERVICE_UNAVAILABLE,
        httpx.codes.GATEWAY_TIMEOUT,
    }
)


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    endpoint: str
    ttl: CacheTTL
    method: str = "GET"
    path: str | None = None
    """实际请求路径，默认等于 endpoint。两者分开是为了让缓存键用逻辑名。"""
    params: Mapping[str, str | int | float | bool | None] | None = None
    json_body: Mapping[str, object] | None = None
    headers: Mapping[str, str] | None = None
    bypass_cache: bool = False


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    provider: str
    endpoint: str
    url: str
    status_code: int
    data: Any
    from_cache: bool
    cache_age_s: int | None
    duration_ms: int
    retrieved_at: datetime
    attempts: int

    def provenance(self) -> DataProvenance:
        return DataProvenance(
            provider=self.provider,
            endpoint=self.endpoint,
            source_url=self.url,
            retrieved_at=self.retrieved_at,
            is_cached=self.from_cache,
            cache_age_s=self.cache_age_s,
        )


@dataclass
class ProviderStats:
    """进程内计数，供日后 `/debug/providers` 读取。不落库——重启归零可接受。"""

    requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    http_attempts: int = 0
    errors: int = 0


class BaseProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        runtime: ProviderRuntime,
        profile: ProviderProfile | None = None,
        headers: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.profile = profile if profile is not None else profile_for(name)
        self.runtime = runtime
        self.stats = ProviderStats()
        self._headers = {**self.profile.default_headers, **dict(headers or {})}
        self._owns_client = client is None
        timeout = self.profile.timeout
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=self._headers,
            http2=True,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self._base_url = base_url
        self._limiter = runtime.limiter(name, self.profile)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_json(
        self,
        endpoint: str,
        *,
        ttl: CacheTTL,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        headers: Mapping[str, str] | None = None,
        bypass_cache: bool = False,
    ) -> ProviderResponse:
        return await self.request(
            ProviderRequest(
                endpoint=endpoint,
                ttl=ttl,
                params=params,
                headers=headers,
                bypass_cache=bypass_cache,
            )
        )

    async def request(self, req: ProviderRequest) -> ProviderResponse:
        started = self.runtime.clock.monotonic()
        self.stats.requests += 1
        key = cache_key(
            provider=self.name,
            method=req.method,
            endpoint=req.endpoint,
            params=req.params,
            json_body=req.json_body,
        )
        attempts = 0
        cache_hit = False
        status: int | None = None
        error_code: str | None = None

        try:
            if not req.bypass_cache:
                cached = await self.runtime.cache.get(key)
                if cached is not None:
                    cache_hit = True
                    self.stats.cache_hits += 1
                    age_s = max((self.runtime.clock.now_ms() - cached.stored_at_ms) // 1000, 0)
                    status = cached.status_code
                    return ProviderResponse(
                        provider=self.name,
                        endpoint=req.endpoint,
                        url=cached.url,
                        status_code=cached.status_code,
                        data=cached.body,
                        from_cache=True,
                        cache_age_s=age_s,
                        duration_ms=_duration_ms(started, self.runtime.clock.monotonic()),
                        retrieved_at=_from_ms(cached.stored_at_ms),
                        attempts=0,
                    )

            self.stats.cache_misses += 1
            await self.runtime.quota.check(
                self.name,
                daily_limit=self.profile.daily_quota,
                monthly_limit=self.profile.monthly_quota,
            )

            response, attempts = await self._send_with_retries(req)
            status = response.status_code
            data = _parse_json(response, provider=self.name, endpoint=req.endpoint)
            url = str(response.url)
            await self.runtime.cache.set(
                key,
                provider=self.name,
                endpoint=req.endpoint,
                url=url,
                body=data,
                status_code=response.status_code,
                ttl=req.ttl,
            )
            return ProviderResponse(
                provider=self.name,
                endpoint=req.endpoint,
                url=url,
                status_code=response.status_code,
                data=data,
                from_cache=False,
                cache_age_s=None,
                duration_ms=_duration_ms(started, self.runtime.clock.monotonic()),
                retrieved_at=self.runtime.clock.now(),
                attempts=attempts,
            )
        except ProviderError as exc:
            self.stats.errors += 1
            error_code = exc.code.value
            if exc.provider is None:
                exc.provider = self.name
            if exc.endpoint is None:
                exc.endpoint = req.endpoint
            raise
        finally:
            log.info(
                "provider.request",
                provider=self.name,
                endpoint=req.endpoint,
                method=req.method,
                cache_hit=cache_hit,
                attempts=attempts,
                status=status,
                duration_ms=_duration_ms(started, self.runtime.clock.monotonic()),
                error=error_code,
            )

    async def _send_with_retries(self, req: ProviderRequest) -> tuple[httpx.Response, int]:
        policy = self.profile.retry
        attempts = 0

        def wait(state: RetryCallState) -> float:
            return _wait(state, policy)

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(policy.attempts),
            wait=wait,
            retry=retry_if_exception_type(RetryableProviderError),
            reraise=True,
        ):
            with attempt:
                attempts = attempt.retry_state.attempt_number
                return await self._send_once(req), attempts

        raise AssertionError("AsyncRetrying 在 reraise=True 时不应落到这里")

    async def _send_once(self, req: ProviderRequest) -> httpx.Response:
        await self._limiter.acquire()
        await self.runtime.quota.consume(
            self.name,
            daily_limit=self.profile.daily_quota,
            monthly_limit=self.profile.monthly_quota,
        )
        self.stats.http_attempts += 1

        headers = dict(self._headers)
        if req.headers:
            headers.update(req.headers)

        kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": self.profile.timeout,
        }
        if req.params:
            kwargs["params"] = dict(req.params)
        if req.json_body:
            kwargs["json"] = dict(req.json_body)

        try:
            response = await self._client.request(
                req.method,
                req.path or req.endpoint,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise RetryableProviderError(
                ToolErrorCode.TIMEOUT,
                "上游超时",
                provider=self.name,
                endpoint=req.endpoint,
            ) from exc
        except httpx.RequestError as exc:
            raise RetryableProviderError(
                ToolErrorCode.UPSTREAM_ERROR,
                "上游网络错误",
                provider=self.name,
                endpoint=req.endpoint,
            ) from exc

        if (
            response.status_code in _RETRYABLE_STATUS
            or response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR
        ):
            code = ToolErrorCode.UPSTREAM_ERROR
            if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                code = ToolErrorCode.RATE_LIMITED
            elif response.status_code == httpx.codes.REQUEST_TIMEOUT:
                code = ToolErrorCode.TIMEOUT
            raise RetryableProviderError(
                code,
                f"上游 HTTP {response.status_code}",
                status_code=response.status_code,
                retry_after_s=_retry_after(response),
                provider=self.name,
                endpoint=req.endpoint,
            )

        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise ProviderError(
                ToolErrorCode.UPSTREAM_ERROR,
                f"上游 HTTP {response.status_code}",
                retryable=False,
                status_code=response.status_code,
                provider=self.name,
                endpoint=req.endpoint,
            )
        return response


def _wait(state: RetryCallState, policy: RetryPolicy) -> float:
    exc = state.outcome.exception() if state.outcome else None
    if isinstance(exc, RetryableProviderError) and exc.retry_after_s is not None:
        return min(max(exc.retry_after_s, 0.0), policy.retry_after_cap_s)
    if policy.max_wait_s <= 0:
        return 0.0
    # 指数退避 + 全抖动：wait = random(0, min(max, initial * 2^attempt))
    exp = policy.initial_wait_s * (2 ** max(state.attempt_number - 1, 0))
    capped = min(exp, policy.max_wait_s)
    # 用 attempt_number 做抖动来源，避免测试里引入随机失败；生产上每次 attempt
    # 本来就不同。真随机会让契约测试变成"有时超时"。
    jitter = 0.5 + (state.attempt_number % 5) * 0.1
    return capped * jitter


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_json(response: httpx.Response, *, provider: str, endpoint: str) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "上游返回了非 JSON",
            retryable=False,
            status_code=response.status_code,
            provider=provider,
            endpoint=endpoint,
        ) from exc


def _duration_ms(started: float, ended: float) -> int:
    return max(round((ended - started) * 1000), 0)


def _from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)
