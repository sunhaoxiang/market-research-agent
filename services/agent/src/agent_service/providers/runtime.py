"""Provider 运行时：一份 SQLite、一套时钟，所有客户端共享。

限流器按 provider 名索引，多任务打同一源时共用一个桶（D12）。
缓存命中不经过限流、不消耗配额——那正是缓存存在的全部理由。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agent_service.providers.cache import ProviderCache
from agent_service.providers.limits import QuotaTracker, TokenBucket
from agent_service.providers.profiles import ProviderProfile

type SleepFn = Callable[[float], Awaitable[None]]


@dataclass
class Clock:
    """可注入。测试用它把 TTL / 配额窗口变成确定性的，而不是真的 sleep 30 天。"""

    _now_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    _mono: float = field(default_factory=time.monotonic)
    auto: bool = True
    """True = 每次调用读系统时钟；False = 只在 advance 时走动（测试）。"""

    def now_ms(self) -> int:
        if self.auto:
            return int(time.time() * 1000)
        return self._now_ms

    def monotonic(self) -> float:
        if self.auto:
            return time.monotonic()
        return self._mono

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.now_ms() / 1000, tz=UTC)

    def advance_ms(self, delta: int) -> None:
        self.auto = False
        self._now_ms += delta
        self._mono += delta / 1000

    @classmethod
    def frozen(cls, at: datetime | None = None) -> Clock:
        """停在给定时刻。测试用它推进 TTL / 配额窗口，而不是真等。"""
        when = at or datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        return cls(_now_ms=int(when.timestamp() * 1000), _mono=0.0, auto=False)


class ProviderRuntime:
    def __init__(
        self,
        db_path: Path,
        *,
        clock: Clock | None = None,
        sleep: SleepFn | None = None,
    ) -> None:
        self.clock = clock or Clock()
        self.sleep: SleepFn = sleep or asyncio.sleep
        self.cache = ProviderCache(db_path, now_ms=self.clock.now_ms)
        self.quota = QuotaTracker(db_path, now_ms=self.clock.now_ms)
        self._limiters: dict[str, TokenBucket] = {}
        self._db_path = db_path

    async def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        await self.cache.open()
        await self.quota.open()

    async def aclose(self) -> None:
        await self.cache.aclose()
        await self.quota.aclose()

    def limiter(self, name: str, profile: ProviderProfile) -> TokenBucket:
        bucket = self._limiters.get(name)
        if bucket is None:
            bucket = TokenBucket(
                rate_per_second=profile.rate_per_second,
                burst=profile.burst,
                monotonic=self.clock.monotonic,
                sleep=self.sleep,
            )
            self._limiters[name] = bucket
        return bucket
