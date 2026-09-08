"""三表 tools（P4-5 验收）。优先 SEC XBRL，缺期间才退回 FMP。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import MappingProxyType

import pytest

from agent_service.providers.equity import (
    BalanceSheets,
    CashFlowStatements,
    IncomeStatementRow,
    IncomeStatements,
    StockHistory,
    StockPeers,
    StockProfile,
    StockQuote,
)
from agent_service.providers.sec import (
    CompanyFacts,
    FactConcept,
    FactPoint,
    TickerDirectory,
    TickerEntry,
    company_page_url,
)
from agent_service.schemas.tools import DataProvenance, ToolErrorCode
from agent_service.tools.deps import ToolDeps
from agent_service.tools.financials.bindings import FINANCIALS_TOOLS
from agent_service.tools.financials.statements import run_get_income_statement
from agent_service.tools.financials.xbrl import assemble_income
from agent_service.tools.registry import invoke_tool

_NOW = datetime(2026, 9, 8, tzinfo=UTC)
_CIK = "0001045810"
_PAGE = company_page_url("1045810")
_REVENUE = 130_497_000_000.0


def _prov(
    *, provider: str = "sec_edgar", endpoint: str = "/api/xbrl/companyfacts"
) -> DataProvenance:
    return DataProvenance(
        provider=provider,
        endpoint=endpoint,
        retrieved_at=_NOW,
        is_cached=False,
    )


def _point(
    *,
    value: float,
    start: date | None,
    end: date,
    form: str = "10-K",
    fy: int = 2025,
    fp: str = "FY",
    filed: date = date(2025, 2, 26),
    unit: str = "USD",
    accession: str = "0001045810-25-000031",
) -> FactPoint:
    return FactPoint(
        value=value,
        unit=unit,
        end=end,
        start=start,
        filed=filed,
        form=form,
        fy=fy,
        fp=fp,
        accession=accession,
        frame=None,
    )


def _concept(tag: str, *points: FactPoint) -> FactConcept:
    return FactConcept(
        taxonomy="us-gaap",
        tag=tag,
        label=tag,
        description=None,
        points=points,
    )


def _facts(*concepts: FactConcept) -> CompanyFacts:
    return CompanyFacts(
        cik=_CIK,
        name="NVIDIA CORP",
        concepts=MappingProxyType({("us-gaap", item.tag): item for item in concepts}),
        url=_PAGE,
        provenance=_prov(),
    )


def _directory() -> TickerDirectory:
    return TickerDirectory(
        entries=(TickerEntry(cik=_CIK, ticker="NVDA", title="NVIDIA CORP", url=_PAGE),),
        url="https://www.sec.gov/search-filings",
        provenance=_prov(endpoint="/files/company_tickers.json"),
    )


class FakeSecEdgar:
    def __init__(self, facts: CompanyFacts, directory: TickerDirectory | None = None) -> None:
        self.facts = facts
        self.directory = directory or _directory()
        self.facts_calls: list[str] = []
        self.directory_calls = 0

    async def get_ticker_directory(self) -> TickerDirectory:
        self.directory_calls += 1
        return self.directory

    async def get_company_facts(self, cik: str) -> CompanyFacts:
        self.facts_calls.append(cik)
        return self.facts


class FakeFmp:
    def __init__(self, statements: IncomeStatements | None = None) -> None:
        self.statements = statements
        self.income_calls: list[tuple[str, str, int]] = []

    async def get_income_statements(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> IncomeStatements:
        self.income_calls.append((symbol, period, limit))
        if self.statements is None:
            raise AssertionError("SEC 有期间时不应打 FMP")
        return self.statements

    async def get_quote(self, symbol: str) -> StockQuote:
        del symbol
        raise AssertionError("三表不应打行情")

    async def get_profile(self, symbol: str) -> StockProfile:
        del symbol
        raise AssertionError("三表不应打 profile")

    async def get_historical_prices(self, symbol: str, *, days: int = 30) -> StockHistory:
        del symbol, days
        raise AssertionError("三表不应打历史价")

    async def get_peers(self, symbol: str) -> StockPeers:
        del symbol
        raise AssertionError("三表不应打 peers")

    async def get_balance_sheets(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> BalanceSheets:
        del symbol, period, limit
        raise AssertionError("本测试只覆盖利润表 FMP")

    async def get_cash_flow_statements(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> CashFlowStatements:
        del symbol, period, limit
        raise AssertionError("本测试只覆盖利润表 FMP")


def test_assemble_income_pins_nvda_10k_revenue() -> None:
    facts = _facts(
        _concept(
            "Revenues",
            _point(
                value=_REVENUE,
                start=date(2024, 1, 29),
                end=date(2025, 1, 26),
            ),
        )
    )
    rows = assemble_income(facts, period="annual", limit=4)
    assert len(rows) == 1
    assert rows[0].revenue == pytest.approx(_REVENUE)
    assert rows[0].fiscal_year == 2025
    assert rows[0].fiscal_period == "FY"


def test_assemble_income_drops_ytd_quarter() -> None:
    facts = _facts(
        _concept(
            "Revenues",
            _point(
                value=44_000_000_000,
                start=date(2025, 1, 27),
                end=date(2025, 4, 27),
                form="10-Q",
                fp="Q1",
                fy=2026,
            ),
            _point(
                value=90_000_000_000,
                start=date(2025, 1, 27),
                end=date(2025, 7, 27),
                form="10-Q",
                fp="Q2",
                fy=2026,
            ),
        )
    )
    rows = assemble_income(facts, period="quarterly", limit=4)
    assert len(rows) == 1
    assert rows[0].revenue == pytest.approx(44_000_000_000)
    assert rows[0].fiscal_period == "Q1"


def test_assemble_income_prefers_later_filing() -> None:
    facts = _facts(
        _concept(
            "Revenues",
            _point(
                value=100.0,
                start=date(2024, 1, 29),
                end=date(2025, 1, 26),
                filed=date(2025, 2, 20),
                accession="0001045810-25-000001",
            ),
            _point(
                value=_REVENUE,
                start=date(2024, 1, 29),
                end=date(2025, 1, 26),
                filed=date(2025, 2, 26),
                accession="0001045810-25-000031",
            ),
        )
    )
    rows = assemble_income(facts, period="annual", limit=1)
    assert rows[0].revenue == pytest.approx(_REVENUE)


async def test_income_uses_sec_and_skips_fmp() -> None:
    sec = FakeSecEdgar(
        _facts(
            _concept(
                "Revenues",
                _point(value=_REVENUE, start=date(2024, 1, 29), end=date(2025, 1, 26)),
            )
        )
    )
    fmp = FakeFmp()
    result = await run_get_income_statement(ToolDeps(sec_edgar=sec, fmp=fmp), ticker="  nvda  ")
    assert sec.facts_calls == [_CIK]
    assert fmp.income_calls == []
    assert result.ok is True
    assert result.data is not None
    assert result.data.source == "sec_xbrl"
    assert result.data.ticker == "NVDA"
    assert result.data.cik == _CIK
    assert result.data.rows[0].revenue == pytest.approx(_REVENUE)
    assert result.provenance is not None
    assert result.provenance.source_url == _PAGE
    assert result.quality is not None
    assert "net_income" in result.quality.missing_fields
    assert result.data.rows[0].net_income is None


async def test_income_falls_back_to_fmp_when_xbrl_empty() -> None:
    sec = FakeSecEdgar(_facts())
    fmp = FakeFmp(
        IncomeStatements(
            symbol="NVDA",
            period="annual",
            rows=(
                IncomeStatementRow(
                    period_end=date(2025, 1, 26),
                    fiscal_year=2025,
                    fiscal_period="FY",
                    revenue=_REVENUE,
                    cost_of_revenue=None,
                    gross_profit=None,
                    operating_income=None,
                    net_income=72_880_000_000,
                    eps_basic=None,
                    eps_diluted=None,
                    research_and_development=None,
                    operating_expenses=None,
                    income_tax=None,
                    shares_diluted=None,
                ),
            ),
            url="https://financialmodelingprep.com/financial-summary/NVDA",
            provenance=_prov(provider="fmp", endpoint="/income-statement"),
        )
    )
    result = await run_get_income_statement(ToolDeps(sec_edgar=sec, fmp=fmp), ticker="NVDA")
    assert fmp.income_calls == [("NVDA", "annual", 4)]
    assert result.data is not None
    assert result.data.source == "fmp"
    assert result.data.rows[0].net_income == pytest.approx(72_880_000_000)
    assert result.quality is not None
    assert "SEC XBRL 没有可用期间，改用 FMP" in result.quality.caveats


async def test_blank_ticker_does_not_call_providers() -> None:
    sec = FakeSecEdgar(_facts())
    result = await run_get_income_statement(ToolDeps(sec_edgar=sec), ticker="  ")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT
    assert sec.directory_calls == 0


async def test_missing_sec_and_fmp_is_unavailable() -> None:
    result = await run_get_income_statement(ToolDeps(), ticker="NVDA")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UPSTREAM_ERROR


async def test_invoke_income() -> None:
    sec = FakeSecEdgar(
        _facts(
            _concept(
                "Revenues",
                _point(value=_REVENUE, start=date(2024, 1, 29), end=date(2025, 1, 26)),
            )
        )
    )
    ok = await invoke_tool("get_income_statement", {"ticker": "NVDA"}, ToolDeps(sec_edgar=sec))
    assert ok.ok is True
    missing = await invoke_tool("get_income_statement", {}, ToolDeps(sec_edgar=sec))
    assert missing.ok is False
    assert missing.error is not None
    assert missing.error.code is ToolErrorCode.INVALID_INPUT


def test_function_tool_names_are_stable() -> None:
    assert [tool.name for tool in FINANCIALS_TOOLS] == [
        "get_income_statement",
        "get_balance_sheet",
        "get_cash_flow",
        "get_growth_metrics",
    ]
