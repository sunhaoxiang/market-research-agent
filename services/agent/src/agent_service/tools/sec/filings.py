"""list_sec_filings：ticker → CIK → submissions。不退 FMP。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_service.providers.errors import ProviderError
from agent_service.providers.sec import FilingRef
from agent_service.schemas.tools import (
    DataProvenance,
    DataQuality,
    ToolError,
    ToolErrorCode,
    ToolResult,
)
from agent_service.tools._result import fail_invalid, fail_provider, fail_unavailable
from agent_service.tools.deps import SecEdgarClient, ToolDeps
from agent_service.tools.sec.models import FilingListItem, SecFilingsData
from agent_service.tools.stocks.resolve import disambiguate_tickers, normalize_ticker

_TOOL = "list_sec_filings"
_BLANK = "ticker 为空，请先用 resolve_ticker 拿到代号"
_AMBIGUOUS = "无法唯一确定 ticker，请先用 resolve_ticker"
_DEFAULT_FORMS = ("10-K", "10-Q", "8-K")
_MAX_LIMIT = 40


@dataclass(frozen=True, slots=True)
class CompanyIdentity:
    ticker: str
    cik: str
    url: str


async def run_list_sec_filings(
    deps: ToolDeps,
    *,
    ticker: str,
    form_types: list[str] | None = None,
    limit: int = 10,
) -> ToolResult[SecFilingsData]:
    symbol = normalize_ticker(ticker)
    if not symbol:
        return fail_invalid(_TOOL, _BLANK)
    if limit < 1 or limit > _MAX_LIMIT:
        return fail_invalid(_TOOL, f"limit 必须在 1–{_MAX_LIMIT}")
    forms = _forms(form_types)
    sec = _client(deps, _TOOL)
    if isinstance(sec, ToolResult):
        return sec
    identity = await resolve_company(deps, symbol, _TOOL)
    if isinstance(identity, ToolResult):
        return identity
    try:
        page = await sec.get_submissions(identity.cik)
    except ProviderError as exc:
        return fail_provider(_TOOL, exc)
    items = [_item(row) for row in page.filings_of(*forms)[:limit]]
    missing = ["filings"] if not items else []
    return ToolResult.success(
        SecFilingsData(
            ticker=identity.ticker,
            cik=identity.cik,
            form_types=list(forms),
            filings=items,
            url=page.url,
        ),
        _page_provenance(page.provenance, page.url),
        quality=_quality(missing),
    )


async def resolve_company[T](
    deps: ToolDeps, symbol: str, tool: str
) -> CompanyIdentity | ToolResult[T]:
    sec = _client(deps, tool)
    if isinstance(sec, ToolResult):
        return sec
    try:
        directory = await sec.get_ticker_directory()
    except ProviderError as exc:
        return fail_provider(tool, exc)
    decision = disambiguate_tickers(symbol, directory.entries)
    if decision.resolved is None:
        if decision.candidates:
            return fail_invalid(tool, _AMBIGUOUS)
        return ToolResult.failure(
            ToolError(
                code=ToolErrorCode.NOT_FOUND,
                message=f"没有匹配的股票：{symbol}",
                tool=tool,
                provider="sec_edgar",
                retryable=False,
            )
        )
    entry = decision.resolved
    return CompanyIdentity(ticker=entry.ticker, cik=entry.cik, url=entry.url)


def _client[T](deps: ToolDeps, tool: str) -> SecEdgarClient | ToolResult[T]:
    if deps.sec_edgar is None:
        return fail_unavailable(tool=tool, provider="sec_edgar", message="SEC EDGAR 未初始化")
    return deps.sec_edgar


def _forms(form_types: list[str] | None) -> tuple[str, ...]:
    cleaned = tuple(item.strip().upper() for item in form_types or () if item.strip())
    return cleaned or _DEFAULT_FORMS


def _item(row: FilingRef) -> FilingListItem:
    return FilingListItem(
        accession=row.accession,
        form=row.form,
        filed=row.filed,
        report_date=row.report_date,
        primary_document=row.primary_document,
        description=row.description,
        url=row.url,
    )


def _page_provenance(provenance: DataProvenance, url: str) -> DataProvenance:
    return provenance.model_copy(update={"source_url": url})


def _quality(missing: list[str]) -> DataQuality | None:
    if not missing:
        return None
    return DataQuality(completeness="partial", missing_fields=missing)
