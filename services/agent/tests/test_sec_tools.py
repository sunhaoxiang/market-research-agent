"""SEC tools（P4-8 验收）。能列出申报、抽出 Item 1A/7 正文、按 tag 取 XBRL、财报摘要。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import MappingProxyType

from agent_service.agents.crypto_research import CRYPTO_RESEARCH_TOOLS
from agent_service.providers.sec import (
    CompanyFacts,
    CompanySubmissions,
    FactConcept,
    FactPoint,
    FilingDocument,
    FilingRef,
    TickerDirectory,
    TickerEntry,
    company_page_url,
    filing_document_url,
)
from agent_service.schemas.tools import DataProvenance, ToolErrorCode
from agent_service.tools.deps import ToolDeps
from agent_service.tools.registry import invoke_tool
from agent_service.tools.sec.bindings import SEC_TOOLS
from agent_service.tools.sec.earnings import run_get_earnings_summary
from agent_service.tools.sec.facts import run_get_xbrl_facts
from agent_service.tools.sec.filings import run_list_sec_filings
from agent_service.tools.sec.sections import extract_item, html_to_text, run_get_filing_section

_NOW = datetime(2026, 9, 8, tzinfo=UTC)
_CIK = "0001045810"
_PAGE = company_page_url("1045810")
_TEN_K = "0001045810-25-000031"
_TEN_Q = "0001045810-25-000012"
_EIGHT_K = "0001045810-25-000040"
_REVENUE = 130_497_000_000.0
_FILING_HTML = """<!DOCTYPE html>
<html><body>
<div>ITEM 1. BUSINESS</div>
<p>We design GPUs for accelerated computing.</p>
<div>ITEM 1A. RISK FACTORS</div>
<p>Competition may harm our business. Dependence on a small number of customers is a risk.</p>
<div>ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION
AND RESULTS OF OPERATIONS</div>
<p>Revenue was $130.5 billion in fiscal 2025 compared to $60.9 billion in fiscal 2024.</p>
<div>ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK</div>
<p>We are exposed to interest rate risk.</p>
<div>ITEM 8. FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA</div>
<p>See the consolidated financial statements.</p>
</body></html>
"""


def _prov(*, endpoint: str = "/submissions") -> DataProvenance:
    return DataProvenance(
        provider="sec_edgar",
        endpoint=endpoint,
        retrieved_at=_NOW,
    )


def _directory() -> TickerDirectory:
    return TickerDirectory(
        entries=(TickerEntry(cik=_CIK, ticker="NVDA", title="NVIDIA CORP", url=_PAGE),),
        url="https://www.sec.gov/search-filings",
        provenance=_prov(endpoint="/files/company_tickers.json"),
    )


def _ref(
    *,
    accession: str,
    form: str,
    filed: date,
    report_date: date,
    primary_document: str,
) -> FilingRef:
    return FilingRef(
        accession=accession,
        form=form,
        filed=filed,
        report_date=report_date,
        accepted=None,
        primary_document=primary_document,
        description=form,
        is_xbrl=form != "8-K",
        url=filing_document_url(
            cik="1045810", accession=accession, primary_document=primary_document
        ),
    )


def _submissions() -> CompanySubmissions:
    return CompanySubmissions(
        cik=_CIK,
        name="NVIDIA CORP",
        tickers=("NVDA",),
        exchanges=("Nasdaq",),
        sic="3674",
        sic_description=None,
        fiscal_year_end="0126",
        state_of_incorporation="DE",
        filings=(
            _ref(
                accession=_TEN_K,
                form="10-K",
                filed=date(2025, 2, 26),
                report_date=date(2025, 1, 26),
                primary_document="nvda-20250126.htm",
            ),
            _ref(
                accession=_TEN_Q,
                form="10-Q",
                filed=date(2025, 5, 28),
                report_date=date(2025, 4, 27),
                primary_document="nvda-20250427.htm",
            ),
            _ref(
                accession=_EIGHT_K,
                form="8-K",
                filed=date(2025, 8, 27),
                report_date=date(2025, 8, 27),
                primary_document="nvda-8k.htm",
            ),
        ),
        url=_PAGE,
        provenance=_prov(),
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
    accession: str = _TEN_K,
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
        accession=accession,
        frame=None,
    )


def _annual_facts() -> CompanyFacts:
    start = date(2024, 1, 29)
    end = date(2025, 1, 26)
    concepts = {
        ("us-gaap", "Revenues"): FactConcept(
            taxonomy="us-gaap",
            tag="Revenues",
            label="Revenues",
            description=None,
            points=(_point(value=_REVENUE, start=start, end=end),),
        ),
        ("us-gaap", "OperatingIncomeLoss"): FactConcept(
            taxonomy="us-gaap",
            tag="OperatingIncomeLoss",
            label="Operating income",
            description=None,
            points=(_point(value=81_453_000_000.0, start=start, end=end),),
        ),
        ("us-gaap", "NetIncomeLoss"): FactConcept(
            taxonomy="us-gaap",
            tag="NetIncomeLoss",
            label="Net income",
            description=None,
            points=(_point(value=72_880_000_000.0, start=start, end=end),),
        ),
        ("us-gaap", "EarningsPerShareDiluted"): FactConcept(
            taxonomy="us-gaap",
            tag="EarningsPerShareDiluted",
            label="Diluted EPS",
            description=None,
            points=(
                FactPoint(
                    value=2.94,
                    unit="USD/shares",
                    end=end,
                    start=start,
                    filed=date(2025, 2, 26),
                    form="10-K",
                    fy=2025,
                    fp="FY",
                    accession=_TEN_K,
                    frame=None,
                ),
            ),
        ),
    }
    return CompanyFacts(
        cik=_CIK,
        name="NVIDIA CORP",
        concepts=MappingProxyType(concepts),
        url=_PAGE,
        provenance=_prov(endpoint="/api/xbrl/companyfacts"),
    )


class FakeSecEdgar:
    def __init__(
        self,
        *,
        directory: TickerDirectory | None = None,
        facts: CompanyFacts | None = None,
        submissions: CompanySubmissions | None = None,
        html: str = _FILING_HTML,
    ) -> None:
        self.directory = directory or _directory()
        self.facts = facts or _annual_facts()
        self.submissions = submissions or _submissions()
        self.html = html
        self.directory_calls = 0
        self.facts_calls: list[str] = []
        self.submissions_calls: list[str] = []
        self.document_calls: list[tuple[str, str, str]] = []

    async def get_ticker_directory(self) -> TickerDirectory:
        self.directory_calls += 1
        return self.directory

    async def get_company_facts(self, cik: str) -> CompanyFacts:
        self.facts_calls.append(cik)
        return self.facts

    async def get_submissions(self, cik: str) -> CompanySubmissions:
        self.submissions_calls.append(cik)
        return self.submissions

    async def get_filing_document(
        self, *, cik: str, accession: str, primary_document: str
    ) -> FilingDocument:
        self.document_calls.append((cik, accession, primary_document))
        return FilingDocument(
            cik=_CIK,
            accession=accession,
            primary_document=primary_document,
            html=self.html,
            url=filing_document_url(
                cik=cik, accession=accession, primary_document=primary_document
            ),
            provenance=_prov(endpoint=f"/archives/{accession}"),
        )


def test_extracts_item_1a_and_stops_before_item_7() -> None:
    text = html_to_text(_FILING_HTML)
    item = extract_item(text, "1A")
    assert item is not None
    assert "Competition may harm our business" in item.text
    assert "130.5 billion" not in item.text
    seven = extract_item(text, "7")
    assert seven is not None
    assert "130.5 billion" in seven.text
    assert "interest rate risk" not in seven.text


def test_mda_alias_is_item_7() -> None:
    text = html_to_text(_FILING_HTML)
    item = extract_item(text, "7")
    assert item is not None
    assert item.code == "7"


async def test_list_sec_filings_filters_10k() -> None:
    sec = FakeSecEdgar()
    result = await run_list_sec_filings(ToolDeps(sec_edgar=sec), ticker="NVDA", form_types=["10-K"])
    assert result.ok is True
    assert result.data is not None
    assert result.data.cik == _CIK
    assert [row.form for row in result.data.filings] == ["10-K"]
    assert result.data.filings[0].accession == _TEN_K
    assert result.provenance is not None
    assert result.provenance.source_url == _PAGE


async def test_list_sec_filings_defaults_to_common_forms() -> None:
    result = await run_list_sec_filings(ToolDeps(sec_edgar=FakeSecEdgar()), ticker="NVDA")
    assert result.ok is True
    assert result.data is not None
    assert result.data.form_types == ["10-K", "10-Q", "8-K"]
    assert len(result.data.filings) == 3


async def test_get_filing_section_returns_item_1a() -> None:
    sec = FakeSecEdgar()
    result = await run_get_filing_section(
        ToolDeps(sec_edgar=sec), accession=_TEN_K, section="risk_factors"
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.section == "1A"
    assert result.data.form == "10-K"
    assert "Competition" in result.data.text
    assert result.data.truncated is False
    assert sec.document_calls == [(_CIK, _TEN_K, "nvda-20250126.htm")]
    assert result.provenance is not None
    url = result.provenance.source_url
    assert url is not None
    assert url.endswith("/nvda-20250126.htm")


async def test_get_filing_section_unknown_section_is_invalid() -> None:
    result = await run_get_filing_section(
        ToolDeps(sec_edgar=FakeSecEdgar()), accession=_TEN_K, section="99"
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT


async def test_get_filing_section_missing_accession_is_not_found() -> None:
    result = await run_get_filing_section(
        ToolDeps(sec_edgar=FakeSecEdgar()),
        accession="0001045810-99-000001",
        section="1A",
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.NOT_FOUND


async def test_get_xbrl_facts_pins_nvda_fy2025_revenue() -> None:
    result = await run_get_xbrl_facts(
        ToolDeps(sec_edgar=FakeSecEdgar()),
        ticker="NVDA",
        concepts=["Revenues", "us-gaap:Assets"],
    )
    assert result.ok is True
    assert result.data is not None
    revenue, assets = result.data.concepts
    assert revenue.latest is not None
    assert revenue.latest.value == _REVENUE
    assert assets.latest is None
    assert result.quality is not None
    assert "us-gaap:Assets" in result.quality.missing_fields


async def test_get_xbrl_facts_empty_concepts_is_invalid() -> None:
    result = await run_get_xbrl_facts(
        ToolDeps(sec_edgar=FakeSecEdgar()), ticker="NVDA", concepts=[]
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT


async def test_earnings_summary_uses_annual_when_no_quarter() -> None:
    result = await run_get_earnings_summary(ToolDeps(sec_edgar=FakeSecEdgar()), ticker="NVDA")
    assert result.ok is True
    assert result.data is not None
    assert result.data.revenue == _REVENUE
    assert result.data.form == "10-K"
    assert result.data.accession == _TEN_K
    assert result.data.latest_8k is not None
    assert result.data.latest_8k.form == "8-K"


async def test_sec_tools_need_edgar() -> None:
    result = await run_list_sec_filings(ToolDeps(), ticker="NVDA")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UPSTREAM_ERROR


async def test_invoke_round_trip() -> None:
    deps = ToolDeps(sec_edgar=FakeSecEdgar())
    listed = await invoke_tool("list_sec_filings", {"ticker": "NVDA"}, deps)
    assert listed.ok is True
    section = await invoke_tool("get_filing_section", {"accession": _TEN_K, "section": "7"}, deps)
    assert section.ok is True
    assert section.data is not None
    assert "130.5 billion" in section.data.text


def test_function_tool_names_are_stable() -> None:
    assert [tool.name for tool in SEC_TOOLS] == [
        "list_sec_filings",
        "get_filing_section",
        "get_xbrl_facts",
        "get_earnings_summary",
    ]


def test_crypto_agent_does_not_get_sec_tools() -> None:
    names = {tool.name for tool in CRYPTO_RESEARCH_TOOLS}
    assert "list_sec_filings" not in names
    assert "get_filing_section" not in names
    assert "get_xbrl_facts" not in names
    assert "get_earnings_summary" not in names
