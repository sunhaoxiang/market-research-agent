"""UUIDv7 生成。

为什么是 v7 而不是 v4（DEVELOPMENT_PLAN.md §10.1）：v7 前 48 bit 是 Unix 毫秒时间戳，
因此字典序即时间序。这让 SQLite 主键索引接近顺序写入，也让 `ORDER BY id` 免去额外索引。

Python 3.13 的 stdlib 尚无 `uuid.uuid7()`（3.14 才加入），故在此实现。
结构见 RFC 9562 §5.7：

    |unix_ts_ms (48 bit)                    |ver(4)|rand_a (12) |
    |var(2)|rand_b (62 bit)                                     |

`rand_a` 按 RFC 9562 §6.2 Method 1 用作**同毫秒内的单调计数器**，而非纯随机。
不这样做的话，同一毫秒内生成的多个 id 顺序是随机的——而 `research_events`
正是成批写入的，我们希望 `ORDER BY id` 严格等于插入顺序。
"""

from __future__ import annotations

import os
import threading
import time
from uuid import UUID

_COUNTER_BITS = 12
_COUNTER_MAX = (1 << _COUNTER_BITS) - 1
_COUNTER_SEED_MAX = 1 << (_COUNTER_BITS - 1)
"""新毫秒的计数器起点上限。取一半空间，给同毫秒内的递增留出 2048 的余量。"""


class _MonotonicClock:
    """时间戳 + 同毫秒计数器。加锁是必要的：编排层是 asyncio 与线程池混合。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_ts_ms = -1
        self._counter = 0

    def next(self) -> tuple[int, int]:
        with self._lock:
            ts_ms = time.time_ns() // 1_000_000

            if ts_ms > self._last_ts_ms:
                self._last_ts_ms = ts_ms
                # 随机起点而非固定 0：避免从 id 反推该毫秒内的生成数量
                self._counter = int.from_bytes(os.urandom(2), "big") % _COUNTER_SEED_MAX
            elif self._counter < _COUNTER_MAX:
                self._counter += 1
            else:
                # 同毫秒内耗尽计数空间：借用下一毫秒，宁可时间戳略微超前也要保持单调
                self._last_ts_ms += 1
                self._counter = 0
                ts_ms = self._last_ts_ms

            return ts_ms, self._counter


_clock = _MonotonicClock()


def uuid7() -> UUID:
    """生成一个 UUIDv7。同一进程内严格单调递增。"""
    ts_ms, counter = _clock.next()
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)

    value = (
        (ts_ms & ((1 << 48) - 1)) << 80
        | 0x7 << 76  # version = 7
        | counter << 64  # rand_a 用作单调计数器
        | 0b10 << 62  # variant = RFC 4122
        | rand_b
    )
    return UUID(int=value)


def new_id() -> str:
    """生成字符串形式的 UUIDv7，用于所有业务主键。"""
    return str(uuid7())
