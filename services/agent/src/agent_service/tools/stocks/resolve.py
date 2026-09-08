"""resolve_ticker：代号 / 公司名 / CIK → 10 位 CIK。

底层只调 `get_ticker_directory`（缓存 SEC `company_tickers.json`），
不再包 HTTP。消歧是 tool 层的职责。
"""

from __future__ import annotations

from dataclasses import dataclass
from re import split as re_split

from agent_service.providers.errors import ProviderError
from agent_service.providers.sec import TickerEntry
from agent_service.schemas.tools import DataQuality, ToolError, ToolErrorCode, ToolResult
from agent_service.tools._result import fail_invalid, fail_provider, fail_unavailable
from agent_service.tools.deps import ToolDeps
from agent_service.tools.stocks.models import (
    ResolvedTicker,
    ResolveTickerData,
    TickerMatchKind,
)

_TOOL = "resolve_ticker"
_MAX_CANDIDATES = 8
_CIK_WIDTH = 10


@dataclass(frozen=True, slots=True)
class TickerResolution:
    resolved: TickerEntry | None
    candidates: tuple[TickerEntry, ...]
    kind: TickerMatchKind


def normalize_ticker(value: str) -> str:
    """SEC 用 BRK-B；用户常写 BRK.B。匹配时把点换成连字符。"""
    return value.strip().upper().replace(".", "-")


def disambiguate_tickers(query: str, entries: tuple[TickerEntry, ...]) -> TickerResolution:
    """纯函数：精确 ticker / CIK 优先；名字唯一则自动选取，多条交给 Agent。"""
    q = query.strip()
    fold = q.casefold()
    ticker_key = normalize_ticker(q)

    cik_hits = _cik_hits(q, entries)
    if cik_hits is not None:
        return cik_hits

    by_ticker = tuple(item for item in entries if normalize_ticker(item.ticker) == ticker_key)
    decided = _decide(by_ticker, TickerMatchKind.EXACT_TICKER)
    if decided is not None:
        return decided

    exact_name = tuple(item for item in entries if item.title.casefold() == fold)
    decided = _decide(exact_name, TickerMatchKind.EXACT_NAME)
    if decided is not None:
        return decided

    prefixes = tuple(item for item in entries if item.title.casefold().startswith(fold))
    decided = _decide(prefixes, TickerMatchKind.UNIQUE_NAME)
    if decided is not None:
        return decided

    tokens = tuple(item for item in entries if fold in _title_tokens(item.title))
    decided = _decide(tokens, TickerMatchKind.UNIQUE_NAME)
    if decided is not None:
        return decided

    return TickerResolution(None, (), TickerMatchKind.AMBIGUOUS)


def _decide(pool: tuple[TickerEntry, ...], unique_kind: TickerMatchKind) -> TickerResolution | None:
    if not pool:
        return None
    if len(pool) == 1:
        return _picked(pool[0], pool, unique_kind)
    return TickerResolution(None, _cap(None, pool), TickerMatchKind.AMBIGUOUS)


async def run_resolve_ticker(deps: ToolDeps, *, query: str) -> ToolResult[ResolveTickerData]:
    q = query.strip()
    if not q:
        return fail_invalid(_TOOL, "查询词为空")
    if deps.sec_edgar is None:
        return fail_unavailable(
            tool=_TOOL,
            provider="sec_edgar",
            message="SEC EDGAR 未初始化",
        )
    try:
        directory = await deps.sec_edgar.get_ticker_directory()
    except ProviderError as exc:
        return fail_provider(_TOOL, exc)

    decision = disambiguate_tickers(q, directory.entries)
    if decision.resolved is None and not decision.candidates:
        return ToolResult.failure(
            ToolError(
                code=ToolErrorCode.NOT_FOUND,
                message=f"没有匹配的股票：{q}",
                tool=_TOOL,
                provider="sec_edgar",
                retryable=False,
            )
        )

    resolved = None if decision.resolved is None else _ticker(decision.resolved)
    candidates = [_ticker(item) for item in decision.candidates]
    provenance = directory.provenance
    if resolved is not None:
        provenance = provenance.model_copy(update={"source_url": resolved.url})
    return ToolResult.success(
        ResolveTickerData(
            query=q,
            match=decision.kind,
            resolved=resolved,
            candidates=candidates,
        ),
        provenance,
        quality=_quality(decision),
    )


def _cik_hits(query: str, entries: tuple[TickerEntry, ...]) -> TickerResolution | None:
    raw = query.strip().upper()
    if raw.startswith("CIK"):
        raw = raw[3:].strip()
    if not raw.isdigit() or int(raw) <= 0 or len(raw) > _CIK_WIDTH:
        return None
    digits = raw.lstrip("0")
    if not digits:
        return None
    cik10 = digits.zfill(_CIK_WIDTH)
    hits = tuple(item for item in entries if item.cik == cik10)
    if len(hits) == 1:
        return _picked(hits[0], hits, TickerMatchKind.EXACT_CIK)
    if len(hits) > 1:
        return TickerResolution(None, _cap(None, hits), TickerMatchKind.AMBIGUOUS)
    return TickerResolution(None, (), TickerMatchKind.AMBIGUOUS)


def _picked(
    winner: TickerEntry, pool: tuple[TickerEntry, ...], kind: TickerMatchKind
) -> TickerResolution:
    return TickerResolution(resolved=winner, candidates=_cap(winner, pool), kind=kind)


def _cap(resolved: TickerEntry | None, hits: tuple[TickerEntry, ...]) -> tuple[TickerEntry, ...]:
    ordered = tuple(sorted(hits, key=lambda item: (item.ticker, item.cik)))
    if resolved is not None:
        rest = tuple(item for item in ordered if item.cik != resolved.cik)
        ordered = (resolved, *rest)
    return ordered[:_MAX_CANDIDATES]


def _title_tokens(title: str) -> frozenset[str]:
    parts = [part for part in re_split(r"[^a-z0-9]+", title.casefold()) if part]
    return frozenset(parts)


def _ticker(item: TickerEntry) -> ResolvedTicker:
    return ResolvedTicker(ticker=item.ticker, cik=item.cik, name=item.title, url=item.url)


def _quality(decision: TickerResolution) -> DataQuality | None:
    extras = len(decision.candidates) - (0 if decision.resolved is None else 1)
    if decision.kind is TickerMatchKind.AMBIGUOUS:
        return DataQuality(
            completeness="partial",
            missing_fields=["resolved"],
            caveats=["多个候选无法唯一确定，请用返回的 ticker 或 CIK 再调 resolve_ticker"],
        )
    if decision.kind is TickerMatchKind.UNIQUE_NAME:
        return DataQuality(
            completeness="full",
            caveats=["按公司名匹配。后续请用 ticker 或 CIK，不要再传简称"],
        )
    if extras > 0:
        return DataQuality(
            completeness="full",
            caveats=[f"还有 {extras} 个其他候选。后续请用 ticker 或 CIK"],
        )
    return None
