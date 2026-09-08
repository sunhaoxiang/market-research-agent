"""股票行情 tools（P4-4 验收）。

包 FMP 现成类型，不解析 JSON、不在这里做 ticker 消歧。
ticker 必须是 resolve_ticker 给出的代号；provenance.source_url 是给人点的页面。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from agent_service.providers.equity import (
    PriceBar,
    StockHistory,
    StockPeer,
    StockPeers,
    StockProfile,
    StockQuote,
)
from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import DataProvenance, ToolErrorCode
from agent_service.tools.deps import ToolDeps
from agent_service.tools.registry import invoke_tool
from agent_service.tools.stocks.bindings import STOCK_TOOLS
from agent_service.tools.stocks.market import (
    run_compare_to_index,
    run_get_company_profile,
    run_get_peers,
    run_get_stock_price_history,
    run_get_stock_quote,
)

_NOW = datetime(2026, 9, 8, 16, 0, tzinfo=UTC)
_PAGE = "https://financialmodelingprep.com/financial-summary/NVDA"
_START = date(2026, 8, 9)
_END = date(2026, 9, 8)


def _prov(*, endpoint: str) -> DataProvenance:
    return DataProvenance(
        provider="fmp",
        endpoint=endpoint,
        source_url="https://financialmodelingprep.com/stable" + endpoint,
        retrieved_at=_NOW,
        is_cached=False,
    )


def _quote(
    *,
    name: str | None = "NVIDIA Corporation",
    change: float | None = 2.1,
    change_pct: float | None = 1.77,
    volume: float | None = 50_000_000.0,
    pe: float | None = 45.2,
    as_of: datetime | None = _NOW,
) -> StockQuote:
    return StockQuote(
        symbol="NVDA",
        name=name,
        price=120.5,
        change=change,
        change_pct=change_pct,
        volume=volume,
        day_low=118.0,
        day_high=122.0,
        year_low=90.0,
        year_high=140.0,
        market_cap=3_000_000_000_000.0,
        open=119.0,
        previous_close=118.4,
        pe=pe,
        eps=2.66,
        exchange="NASDAQ",
        as_of=as_of,
        url=_PAGE,
        provenance=_prov(endpoint="/quote"),
    )


def _profile(*, description: str | None = "GPUs", cik: str | None = "0001045810") -> StockProfile:
    return StockProfile(
        symbol="NVDA",
        name="NVIDIA Corporation",
        description=description,
        cik=cik,
        exchange="NASDAQ",
        industry="Semiconductors",
        sector="Technology",
        country="US",
        currency="USD",
        website="https://www.nvidia.com",
        ceo="Jensen Huang",
        ipo_date=date(1999, 1, 22),
        employees=36_000,
        market_cap=3_000_000_000_000.0,
        beta=1.7,
        is_etf=False,
        is_actively_trading=True,
        url=_PAGE,
        provenance=_prov(endpoint="/profile"),
    )


def _bar(session: date, close: float) -> PriceBar:
    return PriceBar(session=session, open=None, high=None, low=None, close=close, volume=None)


def _history(
    symbol: str,
    bars: tuple[PriceBar, ...],
    *,
    days: int = 30,
) -> StockHistory:
    return StockHistory(
        symbol=symbol,
        days=days,
        start=_START,
        end=_END,
        bars=bars,
        url=f"https://financialmodelingprep.com/financial-summary/{symbol}",
        provenance=_prov(endpoint="/historical-price-eod/full"),
    )


def _peers(*, peers: tuple[StockPeer, ...] | None = None) -> StockPeers:
    items = (
        peers
        if peers is not None
        else (
            StockPeer(
                symbol="AMD",
                name="Advanced Micro Devices",
                price=160.0,
                market_cap=260_000_000_000.0,
                url="https://financialmodelingprep.com/financial-summary/AMD",
            ),
            StockPeer(
                symbol="AVGO",
                name="Broadcom",
                price=170.0,
                market_cap=800_000_000_000.0,
                url="https://financialmodelingprep.com/financial-summary/AVGO",
            ),
        )
    )
    return StockPeers(
        symbol="NVDA",
        peers=items,
        url=_PAGE,
        provenance=_prov(endpoint="/stock-peers"),
    )


class FakeFmp:
    def __init__(
        self,
        *,
        quote: StockQuote | None = None,
        profile: StockProfile | None = None,
        history: StockHistory | None = None,
        histories: dict[str, StockHistory] | None = None,
        peers: StockPeers | None = None,
        error: ProviderError | None = None,
    ) -> None:
        self.quote = quote
        self.profile = profile
        self.history = history
        self.histories = histories or {}
        self.peers = peers
        self.error = error
        self.quote_calls: list[str] = []
        self.profile_calls: list[str] = []
        self.history_calls: list[tuple[str, int]] = []
        self.peer_calls: list[str] = []

    async def get_quote(self, symbol: str) -> StockQuote:
        self.quote_calls.append(symbol)
        if self.error is not None:
            raise self.error
        assert self.quote is not None
        return self.quote

    async def get_profile(self, symbol: str) -> StockProfile:
        self.profile_calls.append(symbol)
        if self.error is not None:
            raise self.error
        assert self.profile is not None
        return self.profile

    async def get_historical_prices(self, symbol: str, *, days: int = 30) -> StockHistory:
        self.history_calls.append((symbol, days))
        if self.error is not None:
            raise self.error
        row = self.histories.get(symbol, self.history)
        assert row is not None
        if days == row.days:
            return row
        return StockHistory(
            symbol=row.symbol,
            days=days,
            start=row.start,
            end=row.end,
            bars=row.bars,
            url=row.url,
            provenance=row.provenance,
        )

    async def get_peers(self, symbol: str) -> StockPeers:
        self.peer_calls.append(symbol)
        if self.error is not None:
            raise self.error
        assert self.peers is not None
        return self.peers

    async def get_ratios_ttm(self, symbol: str) -> object:
        raise AssertionError("P4-4 不应包估值比率")


async def test_quote_maps_and_human_url() -> None:
    fmp = FakeFmp(quote=_quote())
    result = await run_get_stock_quote(ToolDeps(fmp=fmp), ticker="  nvda  ")
    assert fmp.quote_calls == ["NVDA"]
    assert result.ok is True
    assert result.data is not None
    assert result.data.ticker == "NVDA"
    assert result.data.price == 120.5
    assert result.data.url == _PAGE
    assert result.provenance is not None
    assert result.provenance.source_url == _PAGE
    assert result.provenance.as_of == _NOW
    assert result.quality is None


async def test_quote_marks_missing_optional_fields() -> None:
    fmp = FakeFmp(
        quote=_quote(name=None, change=None, change_pct=None, volume=None, pe=None, as_of=None)
    )
    result = await run_get_stock_quote(ToolDeps(fmp=fmp), ticker="NVDA")
    assert result.ok is True
    assert result.data is not None
    assert result.data.price == 120.5
    assert result.data.pe is None
    assert result.quality is not None
    assert result.quality.completeness == "partial"
    assert set(result.quality.missing_fields) == {
        "name",
        "change",
        "change_pct",
        "volume",
        "pe",
        "as_of",
    }


async def test_quote_normalizes_share_class() -> None:
    fmp = FakeFmp(quote=_quote())
    await run_get_stock_quote(ToolDeps(fmp=fmp), ticker="BRK.B")
    assert fmp.quote_calls == ["BRK-B"]


async def test_profile_maps_without_json() -> None:
    fmp = FakeFmp(profile=_profile())
    result = await run_get_company_profile(ToolDeps(fmp=fmp), ticker="NVDA")
    assert fmp.profile_calls == ["NVDA"]
    assert result.data is not None
    assert result.data.cik == "0001045810"
    assert result.data.industry == "Semiconductors"
    assert result.provenance is not None
    assert result.provenance.source_url == _PAGE


async def test_history_maps_bars() -> None:
    bars = (_bar(date(2026, 8, 10), 100.0), _bar(date(2026, 9, 8), 120.5))
    fmp = FakeFmp(history=_history("NVDA", bars))
    result = await run_get_stock_price_history(ToolDeps(fmp=fmp), ticker="NVDA", days=7)
    assert fmp.history_calls == [("NVDA", 7)]
    assert result.data is not None
    assert result.data.days == 7
    assert len(result.data.bars) == 2
    assert result.data.bars[-1].close == 120.5
    assert result.provenance is not None
    assert result.provenance.source_url == _PAGE
    assert result.provenance.as_of == datetime(2026, 9, 8, tzinfo=UTC)


async def test_empty_history_is_not_found() -> None:
    fmp = FakeFmp(history=_history("NVDA", ()))
    result = await run_get_stock_price_history(ToolDeps(fmp=fmp), ticker="NVDA")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.NOT_FOUND


async def test_peers_maps_list() -> None:
    fmp = FakeFmp(peers=_peers())
    result = await run_get_peers(ToolDeps(fmp=fmp), ticker="NVDA")
    assert fmp.peer_calls == ["NVDA"]
    assert result.data is not None
    assert [peer.ticker for peer in result.data.peers] == ["AMD", "AVGO"]
    assert result.data.peers[0].url == "https://financialmodelingprep.com/financial-summary/AMD"
    assert result.quality is None


async def test_empty_peers_is_partial_not_failure() -> None:
    fmp = FakeFmp(peers=_peers(peers=()))
    result = await run_get_peers(ToolDeps(fmp=fmp), ticker="NVDA")
    assert result.ok is True
    assert result.data is not None
    assert result.data.peers == []
    assert result.quality is not None
    assert result.quality.missing_fields == ["peers"]


async def test_compare_to_index_uses_overlapping_closes() -> None:
    nvda = _history(
        "NVDA",
        (
            _bar(date(2026, 8, 9), 99.0),
            _bar(date(2026, 8, 10), 100.0),
            _bar(date(2026, 9, 8), 120.0),
        ),
    )
    spy = _history(
        "SPY",
        (
            _bar(date(2026, 8, 10), 400.0),
            _bar(date(2026, 9, 7), 430.0),
            _bar(date(2026, 9, 8), 440.0),
        ),
    )
    fmp = FakeFmp(histories={"NVDA": nvda, "SPY": spy})
    result = await run_compare_to_index(ToolDeps(fmp=fmp), ticker="nvda", days=30)
    assert set(fmp.history_calls) == {("NVDA", 30), ("SPY", 30)}
    assert result.ok is True
    assert result.data is not None
    assert result.data.ticker == "NVDA"
    assert result.data.index == "SPY"
    assert result.data.start == date(2026, 8, 10)
    assert result.data.end == date(2026, 9, 8)
    assert result.data.n_sessions == 2
    assert result.data.ticker_return == pytest.approx(0.20)
    assert result.data.index_return == pytest.approx(0.10)
    assert result.data.excess_return == pytest.approx(0.10)
    assert result.provenance is not None
    assert result.provenance.source_url == _PAGE


async def test_compare_without_overlap_is_not_found() -> None:
    fmp = FakeFmp(
        histories={
            "NVDA": _history("NVDA", (_bar(date(2026, 8, 10), 100.0),)),
            "SPY": _history("SPY", (_bar(date(2026, 9, 8), 440.0),)),
        }
    )
    result = await run_compare_to_index(ToolDeps(fmp=fmp), ticker="NVDA")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.NOT_FOUND


async def test_compare_same_ticker_and_index_is_invalid() -> None:
    fmp = FakeFmp()
    result = await run_compare_to_index(ToolDeps(fmp=fmp), ticker="SPY", index="spy")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT
    assert fmp.history_calls == []


async def test_blank_ticker_does_not_call_provider() -> None:
    fmp = FakeFmp(quote=_quote())
    result = await run_get_stock_quote(ToolDeps(fmp=fmp), ticker="  ")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT
    assert fmp.quote_calls == []


async def test_missing_provider_is_unavailable() -> None:
    result = await run_get_company_profile(ToolDeps(), ticker="NVDA")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UPSTREAM_ERROR


async def test_provider_not_found_becomes_tool_result() -> None:
    fmp = FakeFmp(error=ProviderError(ToolErrorCode.NOT_FOUND, "gone", retryable=False))
    result = await run_get_stock_quote(ToolDeps(fmp=fmp), ticker="NOPE")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.NOT_FOUND


async def test_invoke_quote() -> None:
    fmp = FakeFmp(quote=_quote())
    ok = await invoke_tool("get_stock_quote", {"ticker": "NVDA"}, ToolDeps(fmp=fmp))
    assert ok.ok is True
    missing = await invoke_tool("get_stock_quote", {}, ToolDeps(fmp=fmp))
    assert missing.ok is False
    assert missing.error is not None
    assert missing.error.code is ToolErrorCode.INVALID_INPUT


async def test_invoke_stock_names() -> None:
    fmp = FakeFmp(
        quote=_quote(),
        profile=_profile(),
        history=_history("NVDA", (_bar(date(2026, 8, 10), 100.0), _bar(date(2026, 9, 8), 120.0))),
        histories={
            "NVDA": _history(
                "NVDA", (_bar(date(2026, 8, 10), 100.0), _bar(date(2026, 9, 8), 120.0))
            ),
            "SPY": _history("SPY", (_bar(date(2026, 8, 10), 400.0), _bar(date(2026, 9, 8), 440.0))),
        },
        peers=_peers(),
    )
    deps = ToolDeps(fmp=fmp)
    quote = await invoke_tool("get_stock_quote", {"ticker": "NVDA"}, deps)
    profile = await invoke_tool("get_company_profile", {"ticker": "NVDA"}, deps)
    history = await invoke_tool("get_stock_price_history", {"ticker": "NVDA"}, deps)
    peers = await invoke_tool("get_peers", {"ticker": "NVDA"}, deps)
    compare = await invoke_tool("compare_to_index", {"ticker": "NVDA"}, deps)
    assert quote.ok is True
    assert profile.ok is True
    assert history.ok is True
    assert peers.ok is True
    assert compare.ok is True


def test_function_tool_names_are_stable_and_not_crypto_history() -> None:
    assert [tool.name for tool in STOCK_TOOLS] == [
        "resolve_ticker",
        "get_stock_quote",
        "get_company_profile",
        "get_stock_price_history",
        "get_peers",
        "compare_to_index",
    ]
    assert "get_price_history" not in {tool.name for tool in STOCK_TOOLS}
