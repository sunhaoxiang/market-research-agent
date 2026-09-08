"""get_earnings_summary：最近一季利润表数字，没有季报再用年报。不拉 HTML。"""

from __future__ import annotations

from datetime import date

from agent_service.providers.equity import IncomeStatementRow
from agent_service.providers.errors import ProviderError
from agent_service.providers.sec import CompanyFacts, CompanySubmissions, FactPoint
from agent_service.schemas.tools import DataQuality, ToolError, ToolErrorCode, ToolResult
from agent_service.tools._result import fail_invalid, fail_provider
from agent_service.tools.deps import ToolDeps
from agent_service.tools.financials.xbrl import assemble_income
from agent_service.tools.sec.filings import (
    _client,
    _item,
    _page_provenance,
    resolve_company,
)
from agent_service.tools.sec.models import EarningsSummaryData, FilingListItem
from agent_service.tools.stocks.resolve import normalize_ticker

_TOOL = "get_earnings_summary"
_BLANK = "ticker 为空，请先用 resolve_ticker 拿到代号"
_OPTIONAL = ("revenue", "operating_income", "net_income", "eps_diluted")
_REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)


async def run_get_earnings_summary(
    deps: ToolDeps, *, ticker: str
) -> ToolResult[EarningsSummaryData]:
    symbol = normalize_ticker(ticker)
    if not symbol:
        return fail_invalid(_TOOL, _BLANK)
    sec = _client(deps, _TOOL)
    if isinstance(sec, ToolResult):
        return sec
    identity = await resolve_company(deps, symbol, _TOOL)
    if isinstance(identity, ToolResult):
        return identity
    try:
        facts = await sec.get_company_facts(identity.cik)
    except ProviderError as exc:
        return fail_provider(_TOOL, exc)
    row = _latest_row(facts)
    if row is None:
        return ToolResult.failure(
            ToolError(
                code=ToolErrorCode.NOT_FOUND,
                message=f"没有可用的利润表期间：{identity.ticker}",
                tool=_TOOL,
                provider="sec_edgar",
                retryable=False,
            )
        )
    submissions = await _submissions(deps, identity.cik)
    meta = _meta(facts, row.period_end)
    accession = meta.accession if meta else None
    form = (meta.form if meta else None) or (
        "10-Q" if (row.fiscal_period or "").upper().startswith("Q") else "10-K"
    )
    url = _filing_url(submissions, accession, identity.url)
    latest_8k = _latest_8k(submissions)
    missing = [name for name in _OPTIONAL if getattr(row, name, None) is None]
    return ToolResult.success(
        EarningsSummaryData(
            ticker=identity.ticker,
            cik=identity.cik,
            form=form,
            period_end=row.period_end,
            fiscal_year=row.fiscal_year,
            fiscal_period=row.fiscal_period,
            revenue=row.revenue,
            operating_income=row.operating_income,
            net_income=row.net_income,
            eps_diluted=row.eps_diluted,
            accession=accession,
            url=url,
            latest_8k=latest_8k,
        ),
        _page_provenance(facts.provenance, url),
        quality=_quality(missing),
    )


def _latest_row(facts: CompanyFacts) -> IncomeStatementRow | None:
    quarterly = assemble_income(facts, period="quarterly", limit=1)
    if quarterly:
        return quarterly[0]
    annual = assemble_income(facts, period="annual", limit=1)
    return annual[0] if annual else None


async def _submissions(deps: ToolDeps, cik: str) -> CompanySubmissions | None:
    if deps.sec_edgar is None:
        return None
    try:
        return await deps.sec_edgar.get_submissions(cik)
    except ProviderError:
        return None


def _meta(facts: CompanyFacts, end: date) -> FactPoint | None:
    best: FactPoint | None = None
    for tag in _REVENUE_TAGS:
        concept = facts.concept(tag) or facts.concept(tag, taxonomy="ifrs-full")
        if concept is None:
            continue
        for point in concept.points:
            if point.end != end:
                continue
            if best is None or _newer(point, best):
                best = point
    return best


def _newer(left: FactPoint, right: FactPoint) -> bool:
    left_filed = left.filed or date.min
    right_filed = right.filed or date.min
    if left_filed != right_filed:
        return left_filed > right_filed
    return (left.accession or "") > (right.accession or "")


def _filing_url(
    submissions: CompanySubmissions | None, accession: str | None, fallback: str
) -> str:
    if submissions is None or not accession:
        return fallback
    for row in submissions.filings:
        if row.accession == accession and row.url:
            return row.url
    return fallback


def _latest_8k(submissions: CompanySubmissions | None) -> FilingListItem | None:
    if submissions is None:
        return None
    eights = submissions.filings_of("8-K")
    if not eights:
        return None
    return _item(eights[0])


def _quality(missing: list[str]) -> DataQuality | None:
    if not missing:
        return None
    return DataQuality(completeness="partial", missing_fields=missing)
