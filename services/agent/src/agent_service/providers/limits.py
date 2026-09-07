"""令牌桶限流 + 日/月配额计数。

两件事情故意分开：
- 令牌桶是**节奏**：把突发打散，避免撞上 429。可以等。
- 配额是**预算**：用完就是用完，再等也不会多出额度。必须快速失败
  （`QUOTA_EXHAUSTED`），让 Agent 走 data gap，而不是卡在那里烧任务超时。

配额落 SQLite：进程重启不能把当天已用次数清零，否则 FMP 的 250/day
会被重启成倍放大。令牌桶只活在内存里——重启后从满桶开始是合理的。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import aiosqlite

from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import ToolErrorCode

type SleepFn = Callable[[float], Awaitable[None]]


class NowMs(Protocol):
    def __call__(self) -> int: ...


class MonotonicFn(Protocol):
    def __call__(self) -> float: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_quota (
    provider     TEXT NOT NULL,
    window       TEXT NOT NULL,
    window_start TEXT NOT NULL,
    used         INTEGER NOT NULL,
    PRIMARY KEY (provider, window, window_start)
);
"""


@dataclass(frozen=True, slots=True)
class QuotaWindow:
    daily_used: int | None = None
    daily_limit: int | None = None
    monthly_used: int | None = None
    monthly_limit: int | None = None

    @property
    def exhausted(self) -> bool:
        if self.daily_limit is not None and (self.daily_used or 0) >= self.daily_limit:
            return True
        return self.monthly_limit is not None and (self.monthly_used or 0) >= self.monthly_limit

    @property
    def daily_remaining(self) -> int | None:
        if self.daily_limit is None:
            return None
        return max(self.daily_limit - (self.daily_used or 0), 0)

    @property
    def monthly_remaining(self) -> int | None:
        if self.monthly_limit is None:
            return None
        return max(self.monthly_limit - (self.monthly_used or 0), 0)


class TokenBucket:
    """asyncio 令牌桶。`rate_per_second is None` 表示不限流。"""

    def __init__(
        self,
        *,
        rate_per_second: float | None,
        burst: int,
        monotonic: MonotonicFn,
        sleep: SleepFn,
        acquire_timeout_s: float = 30.0,
    ) -> None:
        if burst < 1:
            msg = "burst 至少为 1"
            raise ValueError(msg)
        if rate_per_second is not None and rate_per_second <= 0:
            msg = "rate_per_second 必须为正，不限流请传 None"
            raise ValueError(msg)
        self._rate = rate_per_second
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._monotonic = monotonic
        self._sleep = sleep
        self._acquire_timeout_s = acquire_timeout_s
        self._updated = monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        if self._rate is None:
            return

        deadline = self._monotonic() + self._acquire_timeout_s
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait_s = (tokens - self._tokens) / self._rate

            remaining = deadline - self._monotonic()
            if wait_s > remaining:
                raise ProviderError(
                    ToolErrorCode.RATE_LIMITED,
                    "本地令牌桶等待超时，上游尚未发出请求",
                    retryable=True,
                )
            await self._sleep(max(wait_s, 0.0))

    def _refill(self) -> None:
        if self._rate is None:
            return
        now = self._monotonic()
        elapsed = max(now - self._updated, 0.0)
        self._updated = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)


class QuotaTracker:
    """日/月配额。窗口按 UTC 切，略保守于部分供应商的当地午夜重置。"""

    def __init__(self, path: Path, *, now_ms: NowMs) -> None:
        self._path = path
        self._now_ms = now_ms
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if self._db is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def aclose(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _require(self) -> aiosqlite.Connection:
        if self._db is None:
            msg = "QuotaTracker.open() 尚未调用"
            raise RuntimeError(msg)
        return self._db

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._now_ms() / 1000, tz=UTC)

    def _starts(self) -> tuple[str, str]:
        now = self._now()
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")

    async def snapshot(
        self,
        provider: str,
        *,
        daily_limit: int | None,
        monthly_limit: int | None,
    ) -> QuotaWindow:
        day_start, month_start = self._starts()
        daily_used = await self._read(provider, "day", day_start) if daily_limit else None
        monthly_used = await self._read(provider, "month", month_start) if monthly_limit else None
        return QuotaWindow(
            daily_used=daily_used,
            daily_limit=daily_limit,
            monthly_used=monthly_used,
            monthly_limit=monthly_limit,
        )

    async def check(
        self,
        provider: str,
        *,
        daily_limit: int | None,
        monthly_limit: int | None,
    ) -> None:
        snap = await self.snapshot(provider, daily_limit=daily_limit, monthly_limit=monthly_limit)
        if snap.exhausted:
            raise _exhausted(provider, snap)

    async def consume(
        self,
        provider: str,
        *,
        daily_limit: int | None,
        monthly_limit: int | None,
        n: int = 1,
    ) -> None:
        """先加后判，超了再回滚。并发下最多放过 limit 次，不会漏计。"""
        day_start, month_start = self._starts()
        applied: list[tuple[str, str]] = []
        try:
            if daily_limit is not None:
                used = await self._add(provider, "day", day_start, n)
                applied.append(("day", day_start))
                if used > daily_limit:
                    raise _exhausted(
                        provider,
                        QuotaWindow(daily_used=used, daily_limit=daily_limit),
                    )
            if monthly_limit is not None:
                used = await self._add(provider, "month", month_start, n)
                applied.append(("month", month_start))
                if used > monthly_limit:
                    raise _exhausted(
                        provider,
                        QuotaWindow(monthly_used=used, monthly_limit=monthly_limit),
                    )
        except ProviderError:
            for window, start in reversed(applied):
                await self._add(provider, window, start, -n)
            raise

    async def _read(self, provider: str, window: str, start: str) -> int:
        db = self._require()
        cursor = await db.execute(
            "SELECT used FROM provider_quota "
            "WHERE provider = ? AND window = ? AND window_start = ?",
            (provider, window, start),
        )
        row = await cursor.fetchone()
        return int(row["used"]) if row is not None else 0

    async def _add(self, provider: str, window: str, start: str, n: int) -> int:
        db = self._require()
        cursor = await db.execute(
            """
            INSERT INTO provider_quota (provider, window, window_start, used)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(provider, window, window_start)
            DO UPDATE SET used = MAX(0, used + excluded.used)
            RETURNING used
            """,
            (provider, window, start, n),
        )
        row = await cursor.fetchone()
        await db.commit()
        if row is None:
            return 0
        return int(row["used"])


def _exhausted(provider: str, snap: QuotaWindow) -> ProviderError:
    if snap.daily_limit is not None:
        used = snap.daily_used or 0
        message = f"{provider} 日配额已耗尽（{used}/{snap.daily_limit}）"
    else:
        used = snap.monthly_used or 0
        message = f"{provider} 月配额已耗尽（{used}/{snap.monthly_limit}）"
    return ProviderError(
        ToolErrorCode.QUOTA_EXHAUSTED,
        message,
        retryable=False,
        provider=provider,
    )
