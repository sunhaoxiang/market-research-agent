"""各数据源的默认节奏与配额（§3.6）。

数字取免费档公开上限。真正的供应商可能更松或更紧；这里偏保守：
本地先拦住，好过打上去收获 429 再重试——重试本身也消耗配额。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3
    initial_wait_s: float = 0.5
    max_wait_s: float = 8.0
    retry_after_cap_s: float = 30.0


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    """单个数据源的横切配置。具体客户端（P2-2 起）只补 base_url 与鉴权头。"""

    rate_per_second: float | None = 2.0
    """None = 不限流。"""
    burst: int = 5
    daily_quota: int | None = None
    monthly_quota: int | None = None
    timeout: httpx.Timeout = field(default_factory=_timeout)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    default_headers: dict[str, str] = field(default_factory=dict)


# 名称必须与 /v1/health 的 data_sources 一致，后续 /debug 才能对得上
PROFILES: dict[str, ProviderProfile] = {
    "tavily": ProviderProfile(
        rate_per_second=1.0,
        burst=3,
        monthly_quota=1_000,
    ),
    "coingecko": ProviderProfile(
        rate_per_second=0.5,  # ~30/min
        burst=5,
        monthly_quota=10_000,
    ),
    "defillama": ProviderProfile(
        rate_per_second=5.0,  # 无限额，礼貌限速
        burst=10,
    ),
    "fmp": ProviderProfile(
        rate_per_second=2.0,
        burst=5,
        daily_quota=250,
    ),
    "sec_edgar": ProviderProfile(
        rate_per_second=10.0,  # SEC 公平访问建议
        burst=10,
    ),
    # 不是第三方 API，是我们自己的抓取客户端。礼貌限速，超时比 API 更短：
    # 卡在一个慢页面上不应拖垮整次研究。
    "web_fetch": ProviderProfile(
        rate_per_second=2.0,
        burst=4,
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
    ),
}


def profile_for(name: str) -> ProviderProfile:
    return PROFILES.get(name, ProviderProfile())
