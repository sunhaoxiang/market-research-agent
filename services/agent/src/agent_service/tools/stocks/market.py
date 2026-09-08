"""股票行情 tools。包 FMP 现成类型，不再解析 JSON，也不做 ticker 消歧。"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

from agent_service.providers.equity import (
    PriceBar,
    StockHistory,
    StockPeers,
    StockProfile,
    StockQuote,
)
from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import (
    DataProvenance,
    DataQuality,
    ToolError,
    ToolErrorCode,
    ToolResult,
)
from agent_service.tools._result import fail_invalid, fail_provider, fail_unavailable
from agent_service.tools.deps import ToolDeps
from agent_service.tools.stocks.models import (
    IndexCompareData,
    StockBarData,
    StockPeerData,
    StockPeersData,
    StockPriceHistoryData,
    StockProfileData,
    StockQuoteData,
)
from agent_service.tools.stocks.resolve import normalize_ticker
from agent_service.tools.system.metrics import price_return

_QUOTE = "get_stock_quote"
_PROFILE = "get_company_profile"
_HISTORY = "get_stock_price_history"
_PEERS = "get_peers"
_COMPARE = "compare_to_index"
_BLANK_TICKER = "ticker 为空，请先用 resolve_ticker 拿到代号"
_MIN_OVERLAP_SESSIONS = 2
_QUOTE_OPTIONAL = (
    "name",
    "change",
    "change_pct",
    "volume",
    "day_low",
    "day_high",
    "year_low",
    "year_high",
    "market_cap",
    "open",
    "previous_close",
    "pe",
    "eps",
    "exchange",
    "as_of",
)
_PROFILE_OPTIONAL = (
    "name",
    "description",
    "cik",
    "exchange",
    "industry",
    "sector",
    "country",
    "currency",
    "website",
    "ceo",
    "ipo_date",
    "employees",
    "market_cap",
    "beta",
    "is_etf",
    "is_actively_trading",
)


async def run_get_stock_quote(deps: ToolDeps, *, ticker: str) -> ToolResult[StockQuoteData]:
    symbol = normalize_ticker(ticker)
    if not symbol:
        return fail_invalid(_QUOTE, _BLANK_TICKER)
    if deps.fmp is None:
        return fail_unavailable(tool=_QUOTE, provider="fmp", message="FMP 未初始化")
    try:
        quote = await deps.fmp.get_quote(symbol)
    except ProviderError as exc:
        return fail_provider(_QUOTE, exc)
    return ToolResult.success(
        _quote_data(quote),
        _page_provenance(quote.provenance, quote.url, as_of=quote.as_of),
        quality=_missing_quality(quote, _QUOTE_OPTIONAL),
    )


async def run_get_company_profile(deps: ToolDeps, *, ticker: str) -> ToolResult[StockProfileData]:
    symbol = normalize_ticker(ticker)
    if not symbol:
        return fail_invalid(_PROFILE, _BLANK_TICKER)
    if deps.fmp is None:
        return fail_unavailable(tool=_PROFILE, provider="fmp", message="FMP 未初始化")
    try:
        profile = await deps.fmp.get_profile(symbol)
    except ProviderError as exc:
        return fail_provider(_PROFILE, exc)
    return ToolResult.success(
        _profile_data(profile),
        _page_provenance(profile.provenance, profile.url),
        quality=_missing_quality(profile, _PROFILE_OPTIONAL),
    )


async def run_get_stock_price_history(
    deps: ToolDeps, *, ticker: str, days: int = 30
) -> ToolResult[StockPriceHistoryData]:
    symbol = normalize_ticker(ticker)
    if not symbol:
        return fail_invalid(_HISTORY, _BLANK_TICKER)
    if deps.fmp is None:
        return fail_unavailable(tool=_HISTORY, provider="fmp", message="FMP 未初始化")
    try:
        history = await deps.fmp.get_historical_prices(symbol, days=days)
    except ProviderError as exc:
        return fail_provider(_HISTORY, exc)
    if not history.bars:
        return _empty_history(_HISTORY, symbol)
    return ToolResult.success(
        _history_data(history),
        _page_provenance(
            history.provenance,
            history.url,
            as_of=_session_at(history.bars[-1].session),
        ),
    )


async def run_get_peers(deps: ToolDeps, *, ticker: str) -> ToolResult[StockPeersData]:
    symbol = normalize_ticker(ticker)
    if not symbol:
        return fail_invalid(_PEERS, _BLANK_TICKER)
    if deps.fmp is None:
        return fail_unavailable(tool=_PEERS, provider="fmp", message="FMP 未初始化")
    try:
        page = await deps.fmp.get_peers(symbol)
    except ProviderError as exc:
        return fail_provider(_PEERS, exc)
    quality = None
    if not page.peers:
        quality = DataQuality(
            completeness="partial",
            missing_fields=["peers"],
            caveats=["FMP 没有给出可比公司，不要编造同行"],
        )
    return ToolResult.success(
        _peers_data(page),
        _page_provenance(page.provenance, page.url),
        quality=quality,
    )


async def run_compare_to_index(
    deps: ToolDeps, *, ticker: str, index: str = "SPY", days: int = 30
) -> ToolResult[IndexCompareData]:
    parsed = _compare_tickers(ticker, index)
    if isinstance(parsed, ToolResult):
        return parsed
    symbol, benchmark = parsed
    if deps.fmp is None:
        return fail_unavailable(tool=_COMPARE, provider="fmp", message="FMP 未初始化")
    try:
        ticker_hist, index_hist = await asyncio.gather(
            deps.fmp.get_historical_prices(symbol, days=days),
            deps.fmp.get_historical_prices(benchmark, days=days),
        )
    except ProviderError as exc:
        return fail_provider(_COMPARE, exc)
    return _compare_from_histories(
        ticker_hist, index_hist, symbol=symbol, benchmark=benchmark, days=days
    )


def _compare_tickers(ticker: str, index: str) -> tuple[str, str] | ToolResult[IndexCompareData]:
    symbol = normalize_ticker(ticker)
    if not symbol:
        return fail_invalid(_COMPARE, _BLANK_TICKER)
    benchmark = normalize_ticker(index)
    if not benchmark:
        return fail_invalid(_COMPARE, "index 为空")
    if symbol == benchmark:
        return fail_invalid(_COMPARE, "ticker 与 index 不能相同")
    return symbol, benchmark


def _compare_from_histories(
    ticker_hist: StockHistory,
    index_hist: StockHistory,
    *,
    symbol: str,
    benchmark: str,
    days: int,
) -> ToolResult[IndexCompareData]:
    if not ticker_hist.bars or not index_hist.bars:
        return _empty_history(_COMPARE, symbol if not ticker_hist.bars else benchmark)
    aligned = _aligned_closes(ticker_hist.bars, index_hist.bars)
    if aligned is None:
        return _not_found(f"没有足够的重叠交易日来对比 {symbol} 与 {benchmark}")
    start, end, n_sessions, t0, t1, i0, i1 = aligned
    ticker_ret = price_return(t0, t1)
    index_ret = price_return(i0, i1)
    if ticker_ret is None or index_ret is None:
        return _not_found(f"无法计算 {symbol} 相对 {benchmark} 的收益（起点价格无效）")
    return ToolResult.success(
        IndexCompareData(
            ticker=symbol,
            index=benchmark,
            days=days,
            start=start,
            end=end,
            n_sessions=n_sessions,
            ticker_start=t0,
            ticker_end=t1,
            index_start=i0,
            index_end=i1,
            ticker_return=ticker_ret,
            index_return=index_ret,
            excess_return=ticker_ret - index_ret,
            url=ticker_hist.url,
        ),
        _page_provenance(ticker_hist.provenance, ticker_hist.url, as_of=_session_at(end)),
        quality=DataQuality(
            completeness="full",
            caveats=["收益按重叠交易日首尾收盘价计算，不是逐日复利"],
        ),
    )


def _quote_data(quote: StockQuote) -> StockQuoteData:
    return StockQuoteData(
        ticker=quote.symbol,
        name=quote.name,
        price=quote.price,
        change=quote.change,
        change_pct=quote.change_pct,
        volume=quote.volume,
        day_low=quote.day_low,
        day_high=quote.day_high,
        year_low=quote.year_low,
        year_high=quote.year_high,
        market_cap=quote.market_cap,
        open=quote.open,
        previous_close=quote.previous_close,
        pe=quote.pe,
        eps=quote.eps,
        exchange=quote.exchange,
        as_of=quote.as_of,
        url=quote.url,
    )


def _profile_data(profile: StockProfile) -> StockProfileData:
    return StockProfileData(
        ticker=profile.symbol,
        name=profile.name,
        description=profile.description,
        cik=profile.cik,
        exchange=profile.exchange,
        industry=profile.industry,
        sector=profile.sector,
        country=profile.country,
        currency=profile.currency,
        website=profile.website,
        ceo=profile.ceo,
        ipo_date=profile.ipo_date,
        employees=profile.employees,
        market_cap=profile.market_cap,
        beta=profile.beta,
        is_etf=profile.is_etf,
        is_actively_trading=profile.is_actively_trading,
        url=profile.url,
    )


def _history_data(history: StockHistory) -> StockPriceHistoryData:
    return StockPriceHistoryData(
        ticker=history.symbol,
        days=history.days,
        start=history.start,
        end=history.end,
        bars=[
            StockBarData(
                session=bar.session,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
            for bar in history.bars
        ],
        url=history.url,
    )


def _peers_data(page: StockPeers) -> StockPeersData:
    return StockPeersData(
        ticker=page.symbol,
        peers=[
            StockPeerData(
                ticker=peer.symbol,
                name=peer.name,
                price=peer.price,
                market_cap=peer.market_cap,
                url=peer.url,
            )
            for peer in page.peers
        ],
        url=page.url,
    )


def _aligned_closes(
    ticker_bars: tuple[PriceBar, ...],
    index_bars: tuple[PriceBar, ...],
) -> tuple[date, date, int, float, float, float, float] | None:
    ticker_by_session = {bar.session: bar.close for bar in ticker_bars}
    index_by_session = {bar.session: bar.close for bar in index_bars}
    common = sorted(set(ticker_by_session) & set(index_by_session))
    if len(common) < _MIN_OVERLAP_SESSIONS:
        return None
    start, end = common[0], common[-1]
    return (
        start,
        end,
        len(common),
        ticker_by_session[start],
        ticker_by_session[end],
        index_by_session[start],
        index_by_session[end],
    )


def _page_provenance(
    provenance: DataProvenance, url: str, *, as_of: datetime | None = None
) -> DataProvenance:
    return provenance.model_copy(update={"source_url": url, "as_of": as_of})


def _missing_quality(row: object, fields: tuple[str, ...]) -> DataQuality | None:
    missing = [name for name in fields if getattr(row, name) is None]
    if not missing:
        return None
    return DataQuality(completeness="partial", missing_fields=missing)


def _session_at(session: date) -> datetime:
    return datetime(session.year, session.month, session.day, tzinfo=UTC)


def _not_found[T](message: str) -> ToolResult[T]:
    return ToolResult.failure(
        ToolError(
            code=ToolErrorCode.NOT_FOUND,
            message=message,
            tool=_COMPARE,
            provider="fmp",
            retryable=False,
        )
    )


def _empty_history[T](tool: str, symbol: str) -> ToolResult[T]:
    return ToolResult.failure(
        ToolError(
            code=ToolErrorCode.NOT_FOUND,
            message=f"没有价格历史：{symbol}",
            tool=tool,
            provider="fmp",
            retryable=False,
        )
    )
