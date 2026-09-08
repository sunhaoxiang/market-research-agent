"""增长率。从利润表用 Python 算 YoY / QoQ / CAGR / 利润率，不交给 LLM。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from agent_service.schemas.tools import DataProvenance, DataQuality, ToolResult
from agent_service.tools._result import fail_invalid
from agent_service.tools.deps import ToolDeps
from agent_service.tools.financials.models import (
    GrowthMetricsData,
    IncomePeriodData,
    IncomeStatementData,
    MarginPeriodData,
    StatementSource,
)
from agent_service.tools.financials.statements import run_get_income_statement
from agent_service.tools.stocks.resolve import normalize_ticker
from agent_service.tools.system.metrics import cagr, price_return, years_between

_TOOL = "get_growth_metrics"
_BLANK = "ticker 为空，请先用 resolve_ticker 拿到代号"
_DEFAULT_YEARS = 3
_MAX_YEARS = 10
_QUARTER_LIMIT = 5
_MIN_PERIODS = 2
_ANNUAL_GAP = (300, 430)
_QUARTER_GAP = (70, 120)
_OPTIONAL = (
    "revenue_yoy",
    "net_income_yoy",
    "operating_income_yoy",
    "eps_yoy",
    "revenue_qoq",
    "net_income_qoq",
    "revenue_cagr",
    "net_income_cagr",
    "cagr_years",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "gross_margin_yoy",
    "operating_margin_yoy",
    "net_margin_yoy",
)


@dataclass(frozen=True, slots=True)
class _Rates:
    as_of: date | None
    cagr_years: float | None
    revenue_yoy: float | None
    net_income_yoy: float | None
    operating_income_yoy: float | None
    eps_yoy: float | None
    revenue_qoq: float | None
    net_income_qoq: float | None
    revenue_cagr: float | None
    net_income_cagr: float | None
    gross_margin: float | None
    operating_margin: float | None
    net_margin: float | None
    gross_margin_yoy: float | None
    operating_margin_yoy: float | None
    net_margin_yoy: float | None
    margins: tuple[MarginPeriodData, ...]


async def run_get_growth_metrics(
    deps: ToolDeps, *, ticker: str, years: int = _DEFAULT_YEARS
) -> ToolResult[GrowthMetricsData]:
    symbol = normalize_ticker(ticker)
    if not symbol:
        return fail_invalid(_TOOL, _BLANK)
    if years < 1 or years > _MAX_YEARS:
        return fail_invalid(_TOOL, f"years 必须在 1–{_MAX_YEARS}")
    annual, quarterly = await asyncio.gather(
        run_get_income_statement(deps, ticker=symbol, period="annual", limit=years + 1),
        run_get_income_statement(deps, ticker=symbol, period="quarterly", limit=_QUARTER_LIMIT),
    )
    if not annual.ok and not quarterly.ok:
        return _reuse_error(annual)
    primary = annual if annual.ok and annual.data is not None else quarterly
    if not primary.ok or primary.data is None or primary.provenance is None:
        return _reuse_error(annual if not annual.ok else quarterly)
    data = _to_data(
        ticker=primary.data.ticker,
        cik=primary.data.cik,
        source=primary.data.source,
        url=primary.data.url,
        rates=_from_rows(
            [] if not annual.ok or annual.data is None else annual.data.rows,
            [] if not quarterly.ok or quarterly.data is None else quarterly.data.rows,
        ),
    )
    return ToolResult.success(
        data,
        _provenance(primary.provenance, data.as_of),
        quality=_quality(data, _caveats(annual, quarterly)),
    )


def _from_rows(annual: Sequence[IncomePeriodData], quarterly: Sequence[IncomePeriodData]) -> _Rates:
    yoy = _prior_annual(annual)
    qoq = _prior_quarter(quarterly)
    span = _cagr_span(annual)
    latest_margin = _margin(annual[0]) if annual else None
    prior_margin = _margin(yoy[1]) if yoy is not None else None
    oldest, newest = (None, None) if span is None else (span[1], span[2])
    years = None if span is None else span[0]
    as_of = annual[0].period_end if annual else (quarterly[0].period_end if quarterly else None)
    return _Rates(
        as_of=as_of,
        cagr_years=years,
        revenue_yoy=_field_rate(yoy, "revenue"),
        net_income_yoy=_field_rate(yoy, "net_income"),
        operating_income_yoy=_field_rate(yoy, "operating_income"),
        eps_yoy=_eps_rate(yoy),
        revenue_qoq=_field_rate(qoq, "revenue"),
        net_income_qoq=_field_rate(qoq, "net_income"),
        revenue_cagr=_field_cagr(oldest, newest, years, "revenue"),
        net_income_cagr=_field_cagr(oldest, newest, years, "net_income"),
        gross_margin=None if latest_margin is None else latest_margin.gross_margin,
        operating_margin=None if latest_margin is None else latest_margin.operating_margin,
        net_margin=None if latest_margin is None else latest_margin.net_margin,
        gross_margin_yoy=_pp(
            None if latest_margin is None else latest_margin.gross_margin,
            None if prior_margin is None else prior_margin.gross_margin,
        ),
        operating_margin_yoy=_pp(
            None if latest_margin is None else latest_margin.operating_margin,
            None if prior_margin is None else prior_margin.operating_margin,
        ),
        net_margin_yoy=_pp(
            None if latest_margin is None else latest_margin.net_margin,
            None if prior_margin is None else prior_margin.net_margin,
        ),
        margins=tuple(_margin(row) for row in annual),
    )


def _to_data(
    *,
    ticker: str,
    cik: str | None,
    source: StatementSource,
    url: str,
    rates: _Rates,
) -> GrowthMetricsData:
    return GrowthMetricsData(
        ticker=ticker,
        cik=cik,
        source=source,
        url=url,
        as_of=rates.as_of,
        cagr_years=rates.cagr_years,
        revenue_yoy=rates.revenue_yoy,
        net_income_yoy=rates.net_income_yoy,
        operating_income_yoy=rates.operating_income_yoy,
        eps_yoy=rates.eps_yoy,
        revenue_qoq=rates.revenue_qoq,
        net_income_qoq=rates.net_income_qoq,
        revenue_cagr=rates.revenue_cagr,
        net_income_cagr=rates.net_income_cagr,
        gross_margin=rates.gross_margin,
        operating_margin=rates.operating_margin,
        net_margin=rates.net_margin,
        gross_margin_yoy=rates.gross_margin_yoy,
        operating_margin_yoy=rates.operating_margin_yoy,
        net_margin_yoy=rates.net_margin_yoy,
        margins=list(rates.margins),
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _field_rate(pair: tuple[IncomePeriodData, IncomePeriodData] | None, field: str) -> float | None:
    if pair is None:
        return None
    latest = _number(getattr(pair[0], field))
    prior = _number(getattr(pair[1], field))
    if latest is None or prior is None:
        return None
    return price_return(prior, latest)


def _field_cagr(
    oldest: IncomePeriodData | None,
    newest: IncomePeriodData | None,
    years: float | None,
    field: str,
) -> float | None:
    if oldest is None or newest is None or years is None:
        return None
    first = _number(getattr(oldest, field))
    last = _number(getattr(newest, field))
    if first is None or last is None:
        return None
    return cagr(first, last, years)


def _eps_rate(pair: tuple[IncomePeriodData, IncomePeriodData] | None) -> float | None:
    if pair is None:
        return None
    latest = _eps(pair[0])
    prior = _eps(pair[1])
    if latest is None or prior is None:
        return None
    return price_return(prior, latest)


def _eps(row: IncomePeriodData) -> float | None:
    if row.eps_diluted is not None:
        return row.eps_diluted
    return row.eps_basic


def _prior_annual(
    rows: Sequence[IncomePeriodData],
) -> tuple[IncomePeriodData, IncomePeriodData] | None:
    if not rows:
        return None
    latest = rows[0]
    for prior in rows[1:]:
        if _is_prior_year(latest, prior):
            return latest, prior
    return None


def _prior_quarter(
    rows: Sequence[IncomePeriodData],
) -> tuple[IncomePeriodData, IncomePeriodData] | None:
    if len(rows) < _MIN_PERIODS:
        return None
    latest, prior = rows[0], rows[1]
    days = (latest.period_end - prior.period_end).days
    if _QUARTER_GAP[0] <= days <= _QUARTER_GAP[1]:
        return latest, prior
    return None


def _is_prior_year(latest: IncomePeriodData, prior: IncomePeriodData) -> bool:
    if latest.fiscal_year is not None and prior.fiscal_year is not None:
        return latest.fiscal_year == prior.fiscal_year + 1
    days = (latest.period_end - prior.period_end).days
    return _ANNUAL_GAP[0] <= days <= _ANNUAL_GAP[1]


def _cagr_span(
    rows: Sequence[IncomePeriodData],
) -> tuple[float, IncomePeriodData, IncomePeriodData] | None:
    if len(rows) < _MIN_PERIODS:
        return None
    newest, oldest = rows[0], rows[-1]
    if oldest.fiscal_year is not None and newest.fiscal_year is not None:
        delta = newest.fiscal_year - oldest.fiscal_year
        if delta > 0:
            return float(delta), oldest, newest
    span = years_between(_as_datetime(oldest.period_end), _as_datetime(newest.period_end))
    if span is None:
        return None
    return span, oldest, newest


def _margin(row: IncomePeriodData) -> MarginPeriodData:
    revenue = row.revenue
    gross = row.gross_profit
    if gross is None and revenue is not None and row.cost_of_revenue is not None:
        gross = revenue - row.cost_of_revenue
    return MarginPeriodData(
        period_end=row.period_end,
        fiscal_year=row.fiscal_year,
        fiscal_period=row.fiscal_period,
        gross_margin=_ratio(gross, revenue),
        operating_margin=_ratio(row.operating_income, revenue),
        net_margin=_ratio(row.net_income, revenue),
    )


def _ratio(numerator: float | None, revenue: float | None) -> float | None:
    if numerator is None or revenue is None or revenue == 0:
        return None
    return numerator / revenue


def _pp(latest: float | None, prior: float | None) -> float | None:
    if latest is None or prior is None:
        return None
    return latest - prior


def _as_datetime(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _provenance(base: DataProvenance, as_of: date | None) -> DataProvenance:
    return base.model_copy(update={"as_of": None if as_of is None else _as_datetime(as_of)})


def _caveats(
    annual: ToolResult[IncomeStatementData], quarterly: ToolResult[IncomeStatementData]
) -> list[str]:
    notes: list[str] = []
    for result in (annual, quarterly):
        if result.quality is None:
            continue
        for note in result.quality.caveats:
            if note not in notes:
                notes.append(note)
    if (
        annual.ok
        and quarterly.ok
        and annual.data is not None
        and quarterly.data is not None
        and annual.data.source != quarterly.data.source
    ):
        notes.append("年报与季报来源不同，YoY/CAGR 与 QoQ 不是同一套数字")
    return notes


def _quality(data: GrowthMetricsData, caveats: list[str]) -> DataQuality | None:
    missing = [name for name in _OPTIONAL if getattr(data, name) is None]
    if not missing and not caveats:
        return None
    return DataQuality(
        completeness="partial" if missing else "full",
        missing_fields=missing,
        caveats=caveats,
    )


def _reuse_error[T](result: ToolResult[T]) -> ToolResult[GrowthMetricsData]:
    if result.error is None:
        return fail_invalid(_TOOL, "没有可计算的利润表")
    return ToolResult.failure(result.error.model_copy(update={"tool": _TOOL}))
