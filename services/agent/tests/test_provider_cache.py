"""缓存键稳定性与 TTL 过期。HTTP 契约见 test_provider_base.py。"""

from __future__ import annotations

from pathlib import Path

from agent_service.providers.cache import ProviderCache, cache_key
from agent_service.providers.runtime import Clock
from agent_service.providers.ttl import CacheTTL, ttl_seconds


def test_cache_key_is_stable_under_param_reordering() -> None:
    a = cache_key(
        provider="coingecko",
        method="get",
        endpoint="/simple/price",
        params={"ids": "bitcoin", "vs": "usd"},
    )
    b = cache_key(
        provider="coingecko",
        method="GET",
        endpoint="/simple/price",
        params={"vs": "usd", "ids": "bitcoin"},
    )
    assert a == b


def test_cache_key_ignores_empty_params() -> None:
    a = cache_key(provider="x", method="GET", endpoint="/p", params=None)
    b = cache_key(provider="x", method="GET", endpoint="/p", params={})
    assert a == b


def test_cache_key_does_not_embed_the_payload() -> None:
    """键是哈希，不含可逆的查询原文——避免 SQLite 主键里留下用户问题。"""
    key = cache_key(
        provider="tavily",
        method="POST",
        endpoint="/search",
        json_body={"query": "内部未公开的标的列表"},
    )
    assert "内部未公开的标的列表" not in key
    assert len(key) == 64


def test_permanent_ttl_has_no_expiry() -> None:
    assert ttl_seconds(CacheTTL.PERMANENT) is None


async def test_expired_entry_is_a_miss_and_deleted(tmp_path: Path) -> None:
    clock = Clock.frozen()
    store = ProviderCache(tmp_path / "c.db", now_ms=clock.now_ms)
    await store.open()
    key = cache_key(provider="x", method="GET", endpoint="/q")
    await store.set(
        key,
        provider="x",
        endpoint="/q",
        url="https://example.test/q",
        body={"v": 1},
        status_code=200,
        ttl=CacheTTL.REALTIME,
    )
    assert await store.get(key) is not None

    clock.advance_ms(61_000)
    assert await store.get(key) is None
    await store.aclose()


async def test_permanent_entry_survives_a_long_advance(tmp_path: Path) -> None:
    clock = Clock.frozen()
    store = ProviderCache(tmp_path / "c.db", now_ms=clock.now_ms)
    await store.open()
    key = cache_key(provider="sec", method="GET", endpoint="/filing")
    await store.set(
        key,
        provider="sec",
        endpoint="/filing",
        url="https://sec.example.test/filing",
        body={"accession": "1"},
        status_code=200,
        ttl=CacheTTL.PERMANENT,
    )
    clock.advance_ms(100 * 24 * 60 * 60 * 1000)
    hit = await store.get(key)
    assert hit is not None
    assert hit.body == {"accession": "1"}
    await store.aclose()
