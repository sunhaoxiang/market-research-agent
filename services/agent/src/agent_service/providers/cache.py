"""SQLite KV 缓存。键是 (provider, method, endpoint, params) 的稳定哈希。

为什么用 SQLite 而不是 dict：进程重启后缓存还在，这正是免费额度约束下
缓存存在的意义。dict 一重启，当天的 FMP 配额就按"全未命中"再烧一遍。

键不包含 Authorization：包含的话每次换 key（或同一 key 的不同写法）都会
让命中率掉到 0，等于没缓存；哈希里也犯不着留 secret 的痕迹。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import aiosqlite

from agent_service.providers.ttl import CacheTTL, ttl_seconds

_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_cache (
    cache_key   TEXT PRIMARY KEY,
    provider    TEXT NOT NULL,
    endpoint    TEXT NOT NULL,
    url         TEXT NOT NULL,
    body        TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    stored_at   INTEGER NOT NULL,
    expires_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_provider_cache_expires ON provider_cache(expires_at);
"""


class NowMs(Protocol):
    def __call__(self) -> int: ...


@dataclass(frozen=True, slots=True)
class CacheEntry:
    status_code: int
    body: Any
    url: str
    stored_at_ms: int
    expires_at_ms: int | None


def cache_key(
    *,
    provider: str,
    method: str,
    endpoint: str,
    params: Mapping[str, object] | None = None,
    json_body: Mapping[str, object] | None = None,
) -> str:
    """参数顺序不影响键：`{"b":1,"a":2}` 与 `{"a":2,"b":1}` 是同一条缓存。"""
    payload = {
        "p": provider,
        "m": method.upper(),
        "e": endpoint,
        "q": _canonical(params),
        "b": _canonical(json_body),
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _canonical(value: Mapping[str, object] | None) -> object:
    if not value:
        return None
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


class ProviderCache:
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
            msg = "ProviderCache.open() 尚未调用"
            raise RuntimeError(msg)
        return self._db

    async def get(self, key: str) -> CacheEntry | None:
        db = self._require()
        cursor = await db.execute(
            "SELECT status_code, body, url, stored_at, expires_at "
            "FROM provider_cache WHERE cache_key = ?",
            (key,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        expires_at: int | None = row["expires_at"]
        if expires_at is not None and expires_at <= self._now_ms():
            await db.execute("DELETE FROM provider_cache WHERE cache_key = ?", (key,))
            await db.commit()
            return None

        return CacheEntry(
            status_code=int(row["status_code"]),
            body=json.loads(row["body"]),
            url=str(row["url"]),
            stored_at_ms=int(row["stored_at"]),
            expires_at_ms=expires_at,
        )

    async def set(
        self,
        key: str,
        *,
        provider: str,
        endpoint: str,
        url: str,
        body: Any,
        status_code: int,
        ttl: CacheTTL,
    ) -> None:
        db = self._require()
        stored_at = self._now_ms()
        seconds = ttl_seconds(ttl)
        expires_at = None if seconds is None else stored_at + seconds * 1000
        payload = json.dumps(body, ensure_ascii=False, default=str)
        await db.execute(
            """
            INSERT INTO provider_cache
                (cache_key, provider, endpoint, url, body, status_code, stored_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                url = excluded.url,
                body = excluded.body,
                status_code = excluded.status_code,
                stored_at = excluded.stored_at,
                expires_at = excluded.expires_at
            """,
            (key, provider, endpoint, url, payload, status_code, stored_at, expires_at),
        )
        await db.commit()
