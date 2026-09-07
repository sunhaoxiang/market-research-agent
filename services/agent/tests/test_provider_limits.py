"""令牌桶与配额：耗尽必须快速失败，且重启不能把计数清零。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_service.providers.errors import ProviderError
from agent_service.providers.limits import QuotaTracker, TokenBucket
from agent_service.providers.runtime import Clock
from agent_service.schemas.tools import ToolErrorCode


async def test_token_bucket_allows_burst_then_paces() -> None:
    clock = Clock.frozen()

    async def sleep(seconds: float) -> None:
        clock.advance_ms(int(seconds * 1000))

    bucket = TokenBucket(
        rate_per_second=10.0,  # 100ms 一个令牌
        burst=1,
        monotonic=clock.monotonic,
        sleep=sleep,
        acquire_timeout_s=1.0,
    )
    await bucket.acquire()
    await bucket.acquire()
    # 第二次必须等到令牌补回来，时钟应至少走过 ~100ms
    assert clock.monotonic() >= 0.09


async def test_token_bucket_times_out_instead_of_hanging() -> None:
    clock = Clock.frozen()

    async def sleep(seconds: float) -> None:
        clock.advance_ms(int(seconds * 1000))

    bucket = TokenBucket(
        rate_per_second=0.01,
        burst=1,
        monotonic=clock.monotonic,
        sleep=sleep,
        acquire_timeout_s=0.0,
    )
    await bucket.acquire()
    with pytest.raises(ProviderError) as exc:
        await bucket.acquire()
    assert exc.value.code is ToolErrorCode.RATE_LIMITED


async def test_unlimited_bucket_never_waits() -> None:
    clock = Clock.frozen()

    async def sleep(seconds: float) -> None:
        raise AssertionError(f"不限流不应 sleep，却被叫了 {seconds}")

    bucket = TokenBucket(
        rate_per_second=None,
        burst=1,
        monotonic=clock.monotonic,
        sleep=sleep,
    )
    await bucket.acquire()
    await bucket.acquire()


async def test_quota_fail_fast_and_survives_restart(tmp_path: Path) -> None:
    clock = Clock.frozen(datetime(2026, 9, 8, 12, 0, tzinfo=UTC))
    path = tmp_path / "q.db"

    tracker = QuotaTracker(path, now_ms=clock.now_ms)
    await tracker.open()
    await tracker.consume("fmp", daily_limit=2, monthly_limit=None)
    await tracker.consume("fmp", daily_limit=2, monthly_limit=None)
    with pytest.raises(ProviderError) as exc:
        await tracker.consume("fmp", daily_limit=2, monthly_limit=None)
    assert exc.value.code is ToolErrorCode.QUOTA_EXHAUSTED
    await tracker.aclose()

    again = QuotaTracker(path, now_ms=clock.now_ms)
    await again.open()
    snap = await again.snapshot("fmp", daily_limit=2, monthly_limit=None)
    assert snap.daily_used == 2
    assert snap.exhausted
    with pytest.raises(ProviderError):
        await again.check("fmp", daily_limit=2, monthly_limit=None)
    await again.aclose()


async def test_quota_windows_are_independent_across_providers(tmp_path: Path) -> None:
    clock = Clock.frozen()
    tracker = QuotaTracker(tmp_path / "q.db", now_ms=clock.now_ms)
    await tracker.open()
    await tracker.consume("fmp", daily_limit=1, monthly_limit=None)
    # 另一个 provider 不该被连坐
    await tracker.consume("coingecko", daily_limit=1, monthly_limit=None)
    snap = await tracker.snapshot("coingecko", daily_limit=1, monthly_limit=None)
    assert snap.daily_used == 1
    await tracker.aclose()


async def test_daily_quota_resets_on_utc_date_change(tmp_path: Path) -> None:
    start = datetime(2026, 9, 8, 23, 30, tzinfo=UTC)
    clock = Clock.frozen(start)
    tracker = QuotaTracker(tmp_path / "q.db", now_ms=clock.now_ms)
    await tracker.open()
    await tracker.consume("fmp", daily_limit=1, monthly_limit=None)
    with pytest.raises(ProviderError):
        await tracker.consume("fmp", daily_limit=1, monthly_limit=None)

    clock.advance_ms(int(timedelta(hours=1).total_seconds() * 1000))
    # 新的 UTC 日：额度应重置，这一次必须成功
    await tracker.consume("fmp", daily_limit=1, monthly_limit=None)
    with pytest.raises(ProviderError) as exc:
        await tracker.consume("fmp", daily_limit=1, monthly_limit=None)
    assert exc.value.code is ToolErrorCode.QUOTA_EXHAUSTED
    await tracker.aclose()
