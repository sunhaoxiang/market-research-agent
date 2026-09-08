"""来源可靠性分级（§15.2）。

按域名与 `source_type` 判定，不让 LLM 自己标——标高了会在冲突时被优先采用。
名单是已知样本，不是穷尽：没列到的域名保持 `unknown`，仅有此类支撑的 claim
必须降级（Fact Checker / Report Writer 的 prompt 会写明）。
"""

from __future__ import annotations

from urllib.parse import urlsplit

from agent_service.schemas.common import SourceReliability, SourceType

_PRIMARY_HOSTS = frozenset(
    {
        "sec.gov",
        "federalreserve.gov",
        "treasury.gov",
        "cftc.gov",
        "fdic.gov",
        "etherscan.io",
        "arbiscan.io",
        "basescan.org",
        "polygonscan.com",
        "bscscan.com",
        "solscan.io",
        "snowtrace.io",
    }
)
_SECONDARY_HOSTS = frozenset(
    {
        "reuters.com",
        "bloomberg.com",
        "wsj.com",
        "ft.com",
        "economist.com",
        "nytimes.com",
        "apnews.com",
        "washingtonpost.com",
        "bbc.com",
        "bbc.co.uk",
        "cnbc.com",
        "marketwatch.com",
        "barrons.com",
        "fortune.com",
        "theblock.co",
        "coindesk.com",
        "blockworks.co",
        "dlnews.com",
        "thedefiant.io",
        "decrypt.co",
        "github.com",
    }
)
_AGGREGATOR_HOSTS = frozenset(
    {
        "coingecko.com",
        "coinmarketcap.com",
        "defillama.com",
        "financialmodelingprep.com",
        "yahoo.com",
        "investing.com",
        "tradingview.com",
        "dune.com",
        "tokenterminal.com",
        "glassnode.com",
    }
)
_SOCIAL_HOSTS = frozenset(
    {
        "twitter.com",
        "x.com",
        "t.co",
        "reddit.com",
        "medium.com",
        "substack.com",
        "youtube.com",
        "youtu.be",
        "tiktok.com",
        "facebook.com",
        "instagram.com",
        "discord.com",
        "t.me",
        "telegram.org",
        "stocktwits.com",
        "warpcast.com",
    }
)


def classify_reliability(
    url: str,
    *,
    source_type: SourceType = SourceType.WEB,
    domain: str | None = None,
) -> SourceReliability:
    """给出 §15.2 四级之一。冲突时优先 primary 是后续 Agent 的事。"""
    host = _host(domain or url)
    if source_type is SourceType.SOCIAL or _matches(host, _SOCIAL_HOSTS):
        return SourceReliability.UNKNOWN
    if (
        source_type in {SourceType.SEC, SourceType.OFFICIAL, SourceType.DOCS}
        or _matches(host, _PRIMARY_HOSTS)
        or _is_gov(host)
    ):
        return SourceReliability.PRIMARY
    if source_type is SourceType.API or _matches(host, _AGGREGATOR_HOSTS):
        return SourceReliability.AGGREGATOR
    if _matches(host, _SECONDARY_HOSTS):
        return SourceReliability.SECONDARY
    return SourceReliability.UNKNOWN


def _host(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    host = urlsplit(raw).hostname or "" if "://" in raw else raw
    return host.lower().removeprefix("www.")


def _matches(host: str, known: frozenset[str]) -> bool:
    if host in known:
        return True
    return any(host.endswith(f".{name}") for name in known)


def _is_gov(host: str) -> bool:
    return host.endswith(".gov") or host.endswith(".gov.uk")
