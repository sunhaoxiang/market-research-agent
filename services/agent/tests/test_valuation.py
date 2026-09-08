"""估值 tools（P4-7 验收）。TTM 包 FMP；历史分位用 Python 算。"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from agent_service.providers.equity import (
    BalanceSheets,
    CashFlowStatements,
    IncomeStatements,
    StockHistory,
    StockPeers,
    StockProfile,
    StockQuote,
    ValuationRatioHistory,
    ValuationRatioRow,
    ValuationRatios,
)
from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import DataProvenance, ToolErrorCode
from agent_service.tools.deps import ToolDeps
from agent_service.tools.financials.valuation import (
    run_get_valuation_history,
    run_get_valuation_metrics,
)
from agent_service.tools.registry import invoke_tool
from agent_service.tools.system.metrics import range_percentile

_NOW = datetime(2026, 9, 8, 16, 0, tzinfo=UTC)
_PAGE = "https://financialmodelingprep.com/financial-summary/NVDA"


def _prov(*, endpoint: str) -> DataProvenance:
    return DataProvenance(
        provider="fmp",
        endpoint=endpoint,
        retrieved_at=_NOW,
        is_cached=False,
    )


def _ttm(*, pe: float | None = 30.0, pb: float | None = None) -> ValuationRatios:
    return ValuationRatios(
        symbol="NVDA",
        pe=pe,
        pb=pb,
        ps=None,
        ev_ebitda=None,
        dividend_yield=None,
        url=_PAGE,
        provenance=_prov(endpoint="/ratios-ttm"),
    )


def _row(end: date, pe: float | None, *, pb: float | None = None) -> ValuationRatioRow:
    return ValuationRatioRow(
        period_end=end,
        fiscal_year=end.year,
        fiscal_period="Q2",
        pe=pe,
        pb=pb,
        ps=None,
        ev_ebitda=None,
    )


def _history(*rows: ValuationRatioRow) -> ValuationRatioHistory:
    return ValuationRatioHistory(
        symbol="NVDA",
        period="quarterly",
        rows=rows,
        url=_PAGE,
        provenance=_prov(endpoint="/ratios"),
    )


class FakeFmp:
    def __init__(
        self,
        *,
        ttm: ValuationRatios | None = None,
        history: ValuationRatioHistory | None = None,
        error: ProviderError | None = None,
    ) -> None:
        self.ttm = ttm
        self.history = history
        self.error = error
        self.ttm_calls: list[str] = []
        self.history_calls: list[tuple[str, str, int]] = []

    async def get_quote(self, symbol: str) -> StockQuote:
        del symbol
        raise AssertionError("估值不应打行情")

    async def get_profile(self, symbol: str) -> StockProfile:
        del symbol
        raise AssertionError("估值不应打 profile")

    async def get_historical_prices(self, symbol: str, *, days: int = 30) -> StockHistory:
        del symbol, days
        raise AssertionError("估值不应打历史价")

    async def get_peers(self, symbol: str) -> StockPeers:
        del symbol
        raise AssertionError("估值不应打 peers")

    async def get_income_statements(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> IncomeStatements:
        del symbol, period, limit
        raise AssertionError("估值不应拉三表")

    async def get_balance_sheets(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> BalanceSheets:
        del symbol, period, limit
        raise AssertionError("估值不应拉三表")

    async def get_cash_flow_statements(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> CashFlowStatements:
        del symbol, period, limit
        raise AssertionError("估值不应拉三表")

    async def get_ratios_ttm(self, symbol: str) -> ValuationRatios:
        self.ttm_calls.append(symbol)
        if self.error is not None:
            raise self.error
        assert self.ttm is not None
        return self.ttm

    async def get_ratios(
        self, symbol: str, *, period: str = "quarterly", limit: int = 20
    ) -> ValuationRatioHistory:
        self.history_calls.append((symbol, period, limit))
        if self.error is not None:
            raise self.error
        assert self.history is not None
        return self.history


async def test_metrics_maps_ttm_keeps_missing_none() -> None:
    fmp = FakeFmp(
        ttm=ValuationRatios(
            symbol="NVDA",
            pe=45.2,
            pb=32.1,
            ps=24.0,
            ev_ebitda=38.5,
            dividend_yield=None,
            url=_PAGE,
            provenance=_prov(endpoint="/ratios-ttm"),
        )
    )
    result = await run_get_valuation_metrics(ToolDeps(fmp=fmp), ticker="  nvda  ")
    assert fmp.ttm_calls == ["NVDA"]
    assert result.ok is True
    assert result.data is not None
    assert result.data.pe == pytest.approx(45.2)
    assert result.data.dividend_yield is None
    assert result.quality is not None
    assert "dividend_yield" in result.quality.missing_fields
    assert result.provenance is not None
    assert result.provenance.source_url == _PAGE


async def test_history_percentile_matches_hand_calc() -> None:
    rows = (
        _row(date(2025, 7, 27), 50.0),
        _row(date(2025, 4, 27), 40.0),
        _row(date(2025, 1, 26), 30.0),
        _row(date(2024, 10, 27), 20.0),
        _row(date(2024, 7, 28), 10.0),
    )
    fmp = FakeFmp(ttm=_ttm(pe=30.0), history=_history(*rows))
    result = await run_get_valuation_history(ToolDeps(fmp=fmp), ticker="NVDA", years=5)
    assert fmp.history_calls == [("NVDA", "quarterly", 20)]
    assert result.ok is True
    assert result.data is not None
    expected = range_percentile((50.0, 40.0, 30.0, 20.0, 10.0, 30.0), 30.0)
    assert result.data.pe == pytest.approx(30.0)
    assert result.data.pe_percentile == pytest.approx(expected)
    assert expected == pytest.approx(50.0)
    assert result.data.points[0].pe == pytest.approx(50.0)
    assert result.data.pb_percentile is None


async def test_constant_series_has_no_percentile() -> None:
    rows = (_row(date(2025, 7, 27), 30.0), _row(date(2025, 4, 27), 30.0))
    fmp = FakeFmp(ttm=_ttm(pe=30.0), history=_history(*rows))
    result = await run_get_valuation_history(ToolDeps(fmp=fmp), ticker="NVDA")
    assert result.data is not None
    assert result.data.pe_percentile is None
    assert result.quality is not None
    assert "pe_percentile" in result.quality.missing_fields


async def test_blank_ticker_skips_fmp() -> None:
    fmp = FakeFmp()
    result = await run_get_valuation_metrics(ToolDeps(fmp=fmp), ticker="  ")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT
    assert fmp.ttm_calls == []


async def test_missing_fmp_is_unavailable() -> None:
    result = await run_get_valuation_metrics(ToolDeps(), ticker="NVDA")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UPSTREAM_ERROR


async def test_invoke_valuation() -> None:
    fmp = FakeFmp(
        ttm=_ttm(pe=30.0),
        history=_history(_row(date(2025, 7, 27), 40.0), _row(date(2024, 7, 28), 10.0)),
    )
    deps = ToolDeps(fmp=fmp)
    metrics = await invoke_tool("get_valuation_metrics", {"ticker": "NVDA"}, deps)
    assert metrics.ok is True
    history = await invoke_tool("get_valuation_history", {"ticker": "NVDA", "years": 2}, deps)
    assert history.ok is True
    assert history.data is not None
    assert history.data.pe_percentile == pytest.approx(range_percentile((40.0, 10.0, 30.0), 30.0))
    missing = await invoke_tool("get_valuation_metrics", {}, deps)
    assert missing.ok is False
    assert missing.error is not None
    assert missing.error.code is ToolErrorCode.INVALID_INPUT
