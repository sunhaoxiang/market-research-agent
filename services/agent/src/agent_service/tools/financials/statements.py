"""三表 tools。优先 SEC companyfacts，拼不出再用 FMP。不做增长率。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from agent_service.providers.equity import (
    BalanceSheetRow,
    CashFlowRow,
    IncomeStatementRow,
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
from agent_service.tools.financials.models import (
    BalancePeriodData,
    BalanceSheetData,
    CashFlowData,
    CashFlowPeriodData,
    IncomePeriodData,
    IncomeStatementData,
)
from agent_service.tools.financials.xbrl import (
    StatementPeriod,
    assemble_balance,
    assemble_cash_flow,
    assemble_income,
)
from agent_service.tools.stocks.resolve import disambiguate_tickers, normalize_ticker

_INCOME = "get_income_statement"
_BALANCE = "get_balance_sheet"
_CASH = "get_cash_flow"
_BLANK = "ticker 为空，请先用 resolve_ticker 拿到代号"
_MAX_LIMIT = 20
_FALLBACK = "SEC XBRL 没有可用期间，改用 FMP"
_INCOME_OPTIONAL = (
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "eps_basic",
    "eps_diluted",
    "research_and_development",
    "operating_expenses",
    "income_tax",
    "shares_diluted",
)
_BALANCE_OPTIONAL = (
    "cash",
    "current_assets",
    "total_assets",
    "current_liabilities",
    "total_liabilities",
    "long_term_debt",
    "stockholders_equity",
    "inventory",
    "accounts_receivable",
)
_CASH_OPTIONAL = ("operating", "investing", "financing", "capex", "depreciation")

type _Assemble[R] = Callable[..., tuple[R, ...]]
type _FmpFetch[R] = Callable[..., Awaitable[tuple[tuple[R, ...], DataProvenance, str]]]


@dataclass(frozen=True, slots=True)
class _Identity:
    ticker: str
    cik: str
    url: str


async def run_get_income_statement(
    deps: ToolDeps, *, ticker: str, period: str = "annual", limit: int = 4
) -> ToolResult[IncomeStatementData]:
    return await _run(
        deps,
        ticker=ticker,
        period=period,
        limit=limit,
        tool=_INCOME,
        assemble=assemble_income,
        fetch_fmp=_fetch_income,
        to_data=_income_data,
        optional=_INCOME_OPTIONAL,
    )


async def run_get_balance_sheet(
    deps: ToolDeps, *, ticker: str, period: str = "annual", limit: int = 4
) -> ToolResult[BalanceSheetData]:
    return await _run(
        deps,
        ticker=ticker,
        period=period,
        limit=limit,
        tool=_BALANCE,
        assemble=assemble_balance,
        fetch_fmp=_fetch_balance,
        to_data=_balance_data,
        optional=_BALANCE_OPTIONAL,
    )


async def run_get_cash_flow(
    deps: ToolDeps, *, ticker: str, period: str = "annual", limit: int = 4
) -> ToolResult[CashFlowData]:
    return await _run(
        deps,
        ticker=ticker,
        period=period,
        limit=limit,
        tool=_CASH,
        assemble=assemble_cash_flow,
        fetch_fmp=_fetch_cash,
        to_data=_cash_data,
        optional=_CASH_OPTIONAL,
    )


async def _run[R, D](
    deps: ToolDeps,
    *,
    ticker: str,
    period: str,
    limit: int,
    tool: str,
    assemble: _Assemble[R],
    fetch_fmp: _FmpFetch[R],
    to_data: Callable[..., D],
    optional: tuple[str, ...],
) -> ToolResult[D]:
    parsed = _parse_args(ticker, period, limit, tool)
    if isinstance(parsed, ToolResult):
        return parsed
    symbol, fiscal, limit_n = parsed
    identity = await _resolve(deps, symbol, tool)
    if isinstance(identity, ToolResult):
        return identity
    sec = await _from_sec(
        deps, identity=identity, period=fiscal, limit=limit_n, tool=tool, assemble=assemble
    )
    if isinstance(sec, ToolResult):
        return sec
    rows, provenance, url, source = sec
    if not rows:
        fmp = await _from_fmp(
            deps, identity=identity, period=fiscal, limit=limit_n, tool=tool, fetch_fmp=fetch_fmp
        )
        if isinstance(fmp, ToolResult):
            return fmp
        rows, provenance, url, source = fmp
    if not rows or provenance is None or url is None or source is None:
        return _not_found(tool, identity.ticker)
    data = to_data(identity, fiscal, source, rows, url)
    caveats = [_FALLBACK] if source == "fmp" else []
    return ToolResult.success(
        data,
        _page_provenance(provenance, url),
        quality=_quality(rows[0], optional, caveats),
    )


def _parse_args[T](
    ticker: str, period: str, limit: int, tool: str
) -> tuple[str, StatementPeriod, int] | ToolResult[T]:
    symbol = normalize_ticker(ticker)
    if not symbol:
        return fail_invalid(tool, _BLANK)
    fiscal = _fiscal_period(period)
    if fiscal is None:
        return fail_invalid(tool, "period 只能是 annual 或 quarterly")
    if limit < 1 or limit > _MAX_LIMIT:
        return fail_invalid(tool, f"limit 必须在 1–{_MAX_LIMIT}")
    return symbol, fiscal, limit


def _fiscal_period(period: str) -> StatementPeriod | None:
    raw = period.strip().lower()
    if raw in {"annual", "fy", "year"}:
        return "annual"
    if raw in {"quarterly", "quarter", "q"}:
        return "quarterly"
    return None


async def _resolve[T](deps: ToolDeps, symbol: str, tool: str) -> _Identity | ToolResult[T]:
    if deps.sec_edgar is None:
        if deps.fmp is None:
            return fail_unavailable(tool=tool, provider="sec_edgar", message="SEC EDGAR 未初始化")
        return _Identity(ticker=symbol, cik="", url="")
    try:
        directory = await deps.sec_edgar.get_ticker_directory()
    except ProviderError as exc:
        return fail_provider(tool, exc)
    decision = disambiguate_tickers(symbol, directory.entries)
    if decision.resolved is None:
        if decision.candidates:
            return fail_invalid(tool, "无法唯一确定 ticker，请先用 resolve_ticker")
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
    return _Identity(ticker=entry.ticker, cik=entry.cik, url=entry.url)


async def _from_sec[R, T](
    deps: ToolDeps,
    *,
    identity: _Identity,
    period: StatementPeriod,
    limit: int,
    tool: str,
    assemble: _Assemble[R],
) -> tuple[tuple[R, ...], DataProvenance | None, str | None, str | None] | ToolResult[T]:
    if deps.sec_edgar is None or not identity.cik:
        return (), None, None, None
    try:
        facts = await deps.sec_edgar.get_company_facts(identity.cik)
    except ProviderError as exc:
        if exc.code in {ToolErrorCode.NOT_FOUND, ToolErrorCode.PARSE_ERROR}:
            return (), None, None, None
        return fail_provider(tool, exc)
    rows = assemble(facts, period=period, limit=limit)
    if not rows:
        return (), None, None, None
    return rows, facts.provenance, facts.url, "sec_xbrl"


async def _from_fmp[R, T](
    deps: ToolDeps,
    *,
    identity: _Identity,
    period: StatementPeriod,
    limit: int,
    tool: str,
    fetch_fmp: _FmpFetch[R],
) -> tuple[tuple[R, ...], DataProvenance | None, str | None, str | None] | ToolResult[T]:
    if deps.fmp is None:
        return (), None, None, None
    try:
        rows, provenance, url = await fetch_fmp(deps, identity.ticker, period, limit)
    except ProviderError as exc:
        return fail_provider(tool, exc)
    if not rows:
        return (), None, None, None
    return rows, provenance, url, "fmp"


async def _fetch_income(
    deps: ToolDeps, ticker: str, period: StatementPeriod, limit: int
) -> tuple[tuple[IncomeStatementRow, ...], DataProvenance, str]:
    fmp = deps.fmp
    if fmp is None:
        raise ProviderError(ToolErrorCode.UPSTREAM_ERROR, "FMP 未初始化", provider="fmp")
    page = await fmp.get_income_statements(ticker, period=period, limit=limit)
    return page.rows, page.provenance, page.url


async def _fetch_balance(
    deps: ToolDeps, ticker: str, period: StatementPeriod, limit: int
) -> tuple[tuple[BalanceSheetRow, ...], DataProvenance, str]:
    fmp = deps.fmp
    if fmp is None:
        raise ProviderError(ToolErrorCode.UPSTREAM_ERROR, "FMP 未初始化", provider="fmp")
    page = await fmp.get_balance_sheets(ticker, period=period, limit=limit)
    return page.rows, page.provenance, page.url


async def _fetch_cash(
    deps: ToolDeps, ticker: str, period: StatementPeriod, limit: int
) -> tuple[tuple[CashFlowRow, ...], DataProvenance, str]:
    fmp = deps.fmp
    if fmp is None:
        raise ProviderError(ToolErrorCode.UPSTREAM_ERROR, "FMP 未初始化", provider="fmp")
    page = await fmp.get_cash_flow_statements(ticker, period=period, limit=limit)
    return page.rows, page.provenance, page.url


def _income_data(
    identity: _Identity,
    period: str,
    source: Literal["sec_xbrl", "fmp"],
    rows: Sequence[object],
    url: str,
) -> IncomeStatementData:
    return IncomeStatementData(
        ticker=identity.ticker,
        cik=identity.cik or None,
        period=period,
        source=source,
        rows=[IncomePeriodData.model_validate(row, from_attributes=True) for row in rows],
        url=url or identity.url,
    )


def _balance_data(
    identity: _Identity,
    period: str,
    source: Literal["sec_xbrl", "fmp"],
    rows: Sequence[object],
    url: str,
) -> BalanceSheetData:
    return BalanceSheetData(
        ticker=identity.ticker,
        cik=identity.cik or None,
        period=period,
        source=source,
        rows=[BalancePeriodData.model_validate(row, from_attributes=True) for row in rows],
        url=url or identity.url,
    )


def _cash_data(
    identity: _Identity,
    period: str,
    source: Literal["sec_xbrl", "fmp"],
    rows: Sequence[object],
    url: str,
) -> CashFlowData:
    return CashFlowData(
        ticker=identity.ticker,
        cik=identity.cik or None,
        period=period,
        source=source,
        rows=[CashFlowPeriodData.model_validate(row, from_attributes=True) for row in rows],
        url=url or identity.url,
    )


def _page_provenance(provenance: DataProvenance, url: str) -> DataProvenance:
    return provenance.model_copy(update={"source_url": url})


def _quality(row: object, fields: tuple[str, ...], caveats: list[str]) -> DataQuality | None:
    missing = [name for name in fields if getattr(row, name, None) is None]
    if not missing and not caveats:
        return None
    return DataQuality(
        completeness="partial" if missing else "full",
        missing_fields=missing,
        caveats=caveats,
    )


def _not_found[T](tool: str, ticker: str) -> ToolResult[T]:
    return ToolResult.failure(
        ToolError(
            code=ToolErrorCode.NOT_FOUND,
            message=f"没有财务报表：{ticker}",
            tool=tool,
            retryable=False,
        )
    )
