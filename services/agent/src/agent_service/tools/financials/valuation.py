"""估值 tools。包 FMP TTM / 历史比率，分位用 Python 算。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

from agent_service.providers.equity import ValuationRatioRow, ValuationRatios
from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import DataProvenance, DataQuality, ToolResult
from agent_service.tools._result import fail_invalid, fail_provider, fail_unavailable
from agent_service.tools.deps import ToolDeps
from agent_service.tools.financials.models import (
    ValuationHistoryData,
    ValuationMetricsData,
    ValuationPointData,
)
from agent_service.tools.stocks.resolve import normalize_ticker
from agent_service.tools.system.metrics import range_percentile

_METRICS = "get_valuation_metrics"
_HISTORY = "get_valuation_history"
_BLANK = "ticker 为空，请先用 resolve_ticker 拿到代号"
_DEFAULT_YEARS = 5
_MAX_YEARS = 10
_QUARTERS = 4
_MAX_POINTS = 20
_MIN_PERIODS = 2
_YEAR_SLACK = 0.25
_METRICS_OPTIONAL = ("pe", "pb", "ps", "ev_ebitda", "dividend_yield")
_HISTORY_OPTIONAL = (
    "pe",
    "pb",
    "ps",
    "ev_ebitda",
    "pe_percentile",
    "pb_percentile",
    "ps_percentile",
    "ev_ebitda_percentile",
)


async def run_get_valuation_metrics(
    deps: ToolDeps, *, ticker: str
) -> ToolResult[ValuationMetricsData]:
    symbol = normalize_ticker(ticker)
    if not symbol:
        return fail_invalid(_METRICS, _BLANK)
    if deps.fmp is None:
        return fail_unavailable(tool=_METRICS, provider="fmp", message="FMP 未初始化")
    try:
        ratios = await deps.fmp.get_ratios_ttm(symbol)
    except ProviderError as exc:
        return fail_provider(_METRICS, exc)
    data = ValuationMetricsData(
        ticker=ratios.symbol,
        pe=ratios.pe,
        pb=ratios.pb,
        ps=ratios.ps,
        ev_ebitda=ratios.ev_ebitda,
        dividend_yield=ratios.dividend_yield,
        url=ratios.url,
    )
    return ToolResult.success(
        data,
        _page_provenance(ratios.provenance, ratios.url),
        quality=_missing(data, _METRICS_OPTIONAL),
    )


async def run_get_valuation_history(
    deps: ToolDeps, *, ticker: str, years: int = _DEFAULT_YEARS
) -> ToolResult[ValuationHistoryData]:
    symbol = normalize_ticker(ticker)
    if not symbol:
        return fail_invalid(_HISTORY, _BLANK)
    if years < 1 or years > _MAX_YEARS:
        return fail_invalid(_HISTORY, f"years 必须在 1–{_MAX_YEARS}")
    if deps.fmp is None:
        return fail_unavailable(tool=_HISTORY, provider="fmp", message="FMP 未初始化")
    limit = min(years * _QUARTERS, _MAX_POINTS)
    try:
        ttm, page = await asyncio.gather(
            deps.fmp.get_ratios_ttm(symbol),
            deps.fmp.get_ratios(symbol, period="quarterly", limit=limit),
        )
    except ProviderError as exc:
        return fail_provider(_HISTORY, exc)
    data = _history_data(ttm, page.rows, years=years, period=page.period, url=page.url)
    return ToolResult.success(
        data,
        _page_provenance(page.provenance, page.url, as_of=_as_of(data.points)),
        quality=_missing(data, _HISTORY_OPTIONAL, _span_note(page.rows, years)),
    )


def _history_data(
    ttm: ValuationRatios,
    rows: Sequence[ValuationRatioRow],
    *,
    years: int,
    period: str,
    url: str,
) -> ValuationHistoryData:
    points = [_point(row) for row in rows]
    pe = ttm.pe if ttm.pe is not None else _latest(rows, "pe")
    pb = ttm.pb if ttm.pb is not None else _latest(rows, "pb")
    ps = ttm.ps if ttm.ps is not None else _latest(rows, "ps")
    ev_ebitda = ttm.ev_ebitda if ttm.ev_ebitda is not None else _latest(rows, "ev_ebitda")
    return ValuationHistoryData(
        ticker=ttm.symbol,
        years=years,
        period=period,
        pe=pe,
        pb=pb,
        ps=ps,
        ev_ebitda=ev_ebitda,
        pe_percentile=_percentile(rows, "pe", pe),
        pb_percentile=_percentile(rows, "pb", pb),
        ps_percentile=_percentile(rows, "ps", ps),
        ev_ebitda_percentile=_percentile(rows, "ev_ebitda", ev_ebitda),
        points=points,
        url=url,
    )


def _point(row: ValuationRatioRow) -> ValuationPointData:
    return ValuationPointData(
        period_end=row.period_end,
        fiscal_year=row.fiscal_year,
        fiscal_period=row.fiscal_period,
        pe=row.pe,
        pb=row.pb,
        ps=row.ps,
        ev_ebitda=row.ev_ebitda,
    )


def _latest(rows: Sequence[ValuationRatioRow], field: str) -> float | None:
    for row in rows:
        value = getattr(row, field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
    return None


def _percentile(
    rows: Sequence[ValuationRatioRow], field: str, current: float | None
) -> float | None:
    if current is None:
        return None
    values = [
        float(value)
        for row in rows
        if isinstance(value := getattr(row, field), int | float) and not isinstance(value, bool)
    ]
    return range_percentile((*values, current), current)


def _span_note(rows: Sequence[ValuationRatioRow], years: int) -> list[str]:
    if len(rows) < _MIN_PERIODS:
        return []
    newest, oldest = rows[0].period_end, rows[-1].period_end
    span = (newest - oldest).days / 365.25
    if span + _YEAR_SLACK >= years:
        return []
    return [f"历史序列不足 {years} 年（约 {span:.1f} 年），分位按实际覆盖期计算"]


def _as_of(points: Sequence[ValuationPointData]) -> datetime | None:
    if not points:
        return None
    day = points[0].period_end
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _page_provenance(
    provenance: DataProvenance, url: str, *, as_of: datetime | None = None
) -> DataProvenance:
    return provenance.model_copy(update={"source_url": url, "as_of": as_of})


def _missing(
    row: object, fields: tuple[str, ...], caveats: list[str] | None = None
) -> DataQuality | None:
    missing = [name for name in fields if getattr(row, name) is None]
    notes = caveats or []
    if not missing and not notes:
        return None
    return DataQuality(
        completeness="partial" if missing else "full",
        missing_fields=missing,
        caveats=notes,
    )
