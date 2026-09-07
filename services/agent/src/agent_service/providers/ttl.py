"""缓存 TTL 分级（§8.3）。

免费额度是硬约束，TTL 不是过早优化：FMP 250 次/天、CoinGecko 1 万次/月，
一次研究会反复查同一标的。分级的原则是"数据本身多久才会变"，而不是
"我们能接受多旧的数据"——已发布的财报永远不变，实时报价 60 秒内也够用。
"""

from __future__ import annotations

from enum import StrEnum


class CacheTTL(StrEnum):
    REALTIME = "realtime"
    """实时价格 / quote。60s。研究场景不需要秒级。"""

    MARKET = "market"
    """市值 / 供应量等市场数据。5 min。"""

    HISTORY_TODAY = "history_today"
    """含当日未收盘的历史序列。6 h。"""

    HISTORY = "history"
    """已收盘的历史区间。30 d。历史数据不变，但仍给一个上限以免键空间膨胀。"""

    DEFI = "defi"
    """TVL / 费用 / DEX 成交量。30 min。DefiLlama 本身按日更新。"""

    PROFILE = "profile"
    """公司 / 协议简介。7 d。"""

    WEB_SEARCH = "web_search"
    """搜索结果。30 min。"""

    WEB_PAGE = "web_page"
    """网页正文。24 h。"""

    PERMANENT = "permanent"
    """财务报表 / SEC filing（按 accession number 键）。已发布的不会变。"""


# None = 永不过期
TTL_SECONDS: dict[CacheTTL, int | None] = {
    CacheTTL.REALTIME: 60,
    CacheTTL.MARKET: 5 * 60,
    CacheTTL.HISTORY_TODAY: 6 * 60 * 60,
    CacheTTL.HISTORY: 30 * 24 * 60 * 60,
    CacheTTL.DEFI: 30 * 60,
    CacheTTL.PROFILE: 7 * 24 * 60 * 60,
    CacheTTL.WEB_SEARCH: 30 * 60,
    CacheTTL.WEB_PAGE: 24 * 60 * 60,
    CacheTTL.PERMANENT: None,
}


def ttl_seconds(ttl: CacheTTL) -> int | None:
    return TTL_SECONDS[ttl]
