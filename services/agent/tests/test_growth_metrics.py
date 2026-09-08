"""增长率 tools（P4-6 验收）。从利润表用 Python 算 YoY / QoQ / CAGR / 利润率。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import MappingProxyType

import pytest

from agent_service.providers.sec import (
    CompanyFacts,
    CompanySubmissions,
    FactConcept,
    FactPoint,
    FilingDocument,
    TickerDirectory,
    TickerEntry,
    company_page_url,
)
from agent_service.schemas.tools import DataProvenance, ToolErrorCode
from agent_service.tools.deps import ToolDeps
from agent_service.tools.financials.growth import run_get_growth_metrics
from agent_service.tools.registry import invoke_tool

_NOW = datetime(2026, 9, 8, tzinfo=UTC)
_CIK = "0001045810"
_PAGE = company_page_url("1045810")
_FY25_REV = 130_497_000_000.0
_FY24_REV = 60_922_000_000.0


def _prov() -> DataProvenance:
    return DataProvenance(
        provider="sec_edgar",
        endpoint="/api/xbrl/companyfacts",
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
) -> FactPoint:
    return FactPoint(
        value=value,
        unit="USD",
        end=end,
        start=start,
        filed=filed,
        form=form,
        fy=fy,
        fp=fp,
        accession="0001045810-25-000031",
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
        provenance=_prov(),
    )


class FakeSecEdgar:
    def __init__(self, facts: CompanyFacts) -> None:
        self.facts = facts
        self.facts_calls: list[str] = []
        self.directory_calls = 0

    async def get_ticker_directory(self) -> TickerDirectory:
        self.directory_calls += 1
        return _directory()

    async def get_company_facts(self, cik: str) -> CompanyFacts:
        self.facts_calls.append(cik)
        return self.facts

    async def get_submissions(self, cik: str) -> CompanySubmissions:
        del cik
        raise AssertionError("增长率不应拉 submissions")

    async def get_filing_document(
        self, *, cik: str, accession: str, primary_document: str
    ) -> FilingDocument:
        del cik, accession, primary_document
        raise AssertionError("增长率不应拉 filing HTML")


def _annual(
    fy: int, end: date, start: date, *, revenue: float, gross: float, operating: float, net: float
) -> tuple[FactPoint, FactPoint, FactPoint, FactPoint]:
    filed = date(end.year, 2, 26)
    return (
        _point(value=revenue, start=start, end=end, fy=fy, filed=filed),
        _point(value=gross, start=start, end=end, fy=fy, filed=filed),
        _point(value=operating, start=start, end=end, fy=fy, filed=filed),
        _point(value=net, start=start, end=end, fy=fy, filed=filed),
    )


def _round_facts() -> CompanyFacts:
    fy25 = _annual(
        2025,
        date(2025, 1, 26),
        date(2024, 1, 29),
        revenue=121.0,
        gross=48.4,
        operating=36.3,
        net=24.2,
    )
    fy24 = _annual(
        2024,
        date(2024, 1, 28),
        date(2023, 1, 29),
        revenue=110.0,
        gross=38.5,
        operating=22.0,
        net=11.0,
    )
    fy23 = _annual(
        2023,
        date(2023, 1, 29),
        date(2022, 1, 30),
        revenue=100.0,
        gross=30.0,
        operating=15.0,
        net=10.0,
    )
    q2 = _point(
        value=110.0,
        start=date(2025, 4, 28),
        end=date(2025, 7, 27),
        form="10-Q",
        fy=2026,
        fp="Q2",
        filed=date(2025, 8, 28),
    )
    q1 = _point(
        value=100.0,
        start=date(2025, 1, 27),
        end=date(2025, 4, 27),
        form="10-Q",
        fy=2026,
        fp="Q1",
        filed=date(2025, 5, 28),
    )
    return _facts(
        _concept("Revenues", fy25[0], fy24[0], fy23[0], q2, q1),
        _concept("GrossProfit", fy25[1], fy24[1], fy23[1]),
        _concept("OperatingIncomeLoss", fy25[2], fy24[2], fy23[2]),
        _concept("NetIncomeLoss", fy25[3], fy24[3], fy23[3]),
    )


async def test_round_numbers_match_hand_calc() -> None:
    deps = ToolDeps(sec_edgar=FakeSecEdgar(_round_facts()))
    result = await run_get_growth_metrics(deps, ticker="NVDA")
    assert result.ok is True
    assert result.data is not None
    assert result.data.revenue_yoy == pytest.approx(0.1)
    assert result.data.revenue_cagr == pytest.approx(0.1)
    assert result.data.cagr_years == 2
    assert result.data.net_income_yoy == pytest.approx(24.2 / 11.0 - 1.0)
    assert result.data.revenue_qoq == pytest.approx(0.1)
    assert result.data.gross_margin == pytest.approx(0.4)
    assert result.data.operating_margin == pytest.approx(0.3)
    assert result.data.net_margin == pytest.approx(0.2)
    assert result.data.gross_margin_yoy == pytest.approx(0.05)
    assert result.data.source == "sec_xbrl"
    assert result.data.as_of == date(2025, 1, 26)
    assert len(result.data.margins) == 3


async def test_nvda_fy2025_revenue_yoy_matches_10k() -> None:
    facts = _facts(
        _concept(
            "Revenues",
            _point(
                value=_FY25_REV,
                start=date(2024, 1, 29),
                end=date(2025, 1, 26),
                fy=2025,
            ),
            _point(
                value=_FY24_REV,
                start=date(2023, 1, 30),
                end=date(2024, 1, 28),
                fy=2024,
                filed=date(2024, 2, 21),
            ),
        )
    )
    result = await run_get_growth_metrics(ToolDeps(sec_edgar=FakeSecEdgar(facts)), ticker="nvda")
    assert result.data is not None
    expected = _FY25_REV / _FY24_REV - 1.0
    assert result.data.revenue_yoy == pytest.approx(expected)
    assert result.quality is not None
    assert "net_income_yoy" in result.quality.missing_fields


async def test_zero_revenue_is_not_a_rate() -> None:
    facts = _facts(
        _concept(
            "Revenues",
            _point(value=10.0, start=date(2024, 1, 29), end=date(2025, 1, 26), fy=2025),
            _point(
                value=0.0,
                start=date(2023, 1, 30),
                end=date(2024, 1, 28),
                fy=2024,
                filed=date(2024, 2, 21),
            ),
        )
    )
    result = await run_get_growth_metrics(ToolDeps(sec_edgar=FakeSecEdgar(facts)), ticker="NVDA")
    assert result.data is not None
    assert result.data.revenue_yoy is None
    assert result.data.gross_margin is None


async def test_single_year_leaves_yoy_and_cagr_empty() -> None:
    facts = _facts(
        _concept(
            "Revenues",
            _point(value=_FY25_REV, start=date(2024, 1, 29), end=date(2025, 1, 26)),
        )
    )
    result = await run_get_growth_metrics(ToolDeps(sec_edgar=FakeSecEdgar(facts)), ticker="NVDA")
    assert result.ok is True
    assert result.data is not None
    assert result.data.revenue_yoy is None
    assert result.data.revenue_cagr is None
    assert result.data.cagr_years is None
    assert result.data.revenue_qoq is None
    assert result.quality is not None
    assert "revenue_yoy" in result.quality.missing_fields


async def test_blank_ticker_does_not_call_sec() -> None:
    sec = FakeSecEdgar(_facts())
    result = await run_get_growth_metrics(ToolDeps(sec_edgar=sec), ticker="  ")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT
    assert sec.directory_calls == 0
    assert sec.facts_calls == []


async def test_bad_years_is_invalid() -> None:
    result = await run_get_growth_metrics(
        ToolDeps(sec_edgar=FakeSecEdgar(_facts())), ticker="NVDA", years=0
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT


async def test_empty_statements_are_not_found() -> None:
    result = await run_get_growth_metrics(ToolDeps(sec_edgar=FakeSecEdgar(_facts())), ticker="NVDA")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.NOT_FOUND
    assert result.error.tool == "get_growth_metrics"


async def test_invoke_growth_metrics() -> None:
    deps = ToolDeps(sec_edgar=FakeSecEdgar(_round_facts()))
    ok = await invoke_tool("get_growth_metrics", {"ticker": "NVDA", "years": 2}, deps)
    assert ok.ok is True
    assert ok.data is not None
    assert ok.data.revenue_yoy == pytest.approx(0.1)
    missing = await invoke_tool("get_growth_metrics", {}, deps)
    assert missing.ok is False
    assert missing.error is not None
    assert missing.error.code is ToolErrorCode.INVALID_INPUT
