"""从 SEC companyfacts 拼出三表期间。缺 tag 保持 None，不要当成 0。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

from agent_service.providers.equity import (
    BalanceSheetRow,
    CashFlowRow,
    IncomeStatementRow,
)
from agent_service.providers.sec import CompanyFacts, FactPoint

type StatementPeriod = Literal["annual", "quarterly"]
type UnitKind = Literal["usd", "per_share", "shares"]

_ANNUAL_FORMS = frozenset({"10-K", "20-F", "40-F"})
_QUARTER_FORMS = frozenset({"10-Q", "6-K"})
_ANNUAL_DAYS = (300, 400)
_QUARTER_DAYS = (70, 110)

_INCOME_FIELDS: tuple[tuple[str, tuple[str, ...], UnitKind], ...] = (
    (
        "revenue",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ),
        "usd",
    ),
    ("cost_of_revenue", ("CostOfRevenue", "CostOfGoodsAndServicesSold"), "usd"),
    ("gross_profit", ("GrossProfit",), "usd"),
    ("operating_income", ("OperatingIncomeLoss",), "usd"),
    (
        "net_income",
        ("NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"),
        "usd",
    ),
    ("eps_basic", ("EarningsPerShareBasic",), "per_share"),
    ("eps_diluted", ("EarningsPerShareDiluted",), "per_share"),
    ("research_and_development", ("ResearchAndDevelopmentExpense",), "usd"),
    ("operating_expenses", ("OperatingExpenses",), "usd"),
    ("income_tax", ("IncomeTaxExpenseBenefit",), "usd"),
    (
        "shares_diluted",
        ("WeightedAverageNumberOfDilutedSharesOutstanding",),
        "shares",
    ),
)

_BALANCE_FIELDS: tuple[tuple[str, tuple[str, ...], UnitKind], ...] = (
    ("cash", ("CashAndCashEquivalentsAtCarryingValue",), "usd"),
    ("current_assets", ("AssetsCurrent",), "usd"),
    ("total_assets", ("Assets",), "usd"),
    ("current_liabilities", ("LiabilitiesCurrent",), "usd"),
    ("total_liabilities", ("Liabilities",), "usd"),
    ("long_term_debt", ("LongTermDebt", "LongTermDebtNoncurrent"), "usd"),
    (
        "stockholders_equity",
        (
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
        "usd",
    ),
    ("inventory", ("InventoryNet",), "usd"),
    ("accounts_receivable", ("AccountsReceivableNetCurrent",), "usd"),
)

_CASH_FIELDS: tuple[tuple[str, tuple[str, ...], UnitKind], ...] = (
    ("operating", ("NetCashProvidedByUsedInOperatingActivities",), "usd"),
    ("investing", ("NetCashProvidedByUsedInInvestingActivities",), "usd"),
    ("financing", ("NetCashProvidedByUsedInFinancingActivities",), "usd"),
    ("capex", ("PaymentsToAcquirePropertyPlantAndEquipment",), "usd"),
    (
        "depreciation",
        ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization"),
        "usd",
    ),
)

_INCOME_CORE = ("revenue", "net_income")
_BALANCE_CORE = ("total_assets", "stockholders_equity")
_CASH_CORE = ("operating",)


@dataclass(frozen=True, slots=True)
class _FieldSeries:
    name: str
    by_end: dict[date, FactPoint]


def assemble_income(
    facts: CompanyFacts, *, period: StatementPeriod, limit: int
) -> tuple[IncomeStatementRow, ...]:
    series = _series(facts, _INCOME_FIELDS, period=period, instant=False)
    rows: list[IncomeStatementRow] = []
    for end in _period_ends(series, _INCOME_CORE, limit):
        values = _values(series, end)
        meta = _meta(series, end)
        rows.append(
            IncomeStatementRow(
                period_end=end,
                fiscal_year=meta.fy if meta else None,
                fiscal_period=meta.fp if meta else None,
                revenue=_float(values, "revenue"),
                cost_of_revenue=_float(values, "cost_of_revenue"),
                gross_profit=_float(values, "gross_profit"),
                operating_income=_float(values, "operating_income"),
                net_income=_float(values, "net_income"),
                eps_basic=_float(values, "eps_basic"),
                eps_diluted=_float(values, "eps_diluted"),
                research_and_development=_float(values, "research_and_development"),
                operating_expenses=_float(values, "operating_expenses"),
                income_tax=_float(values, "income_tax"),
                shares_diluted=_float(values, "shares_diluted"),
            )
        )
    return tuple(rows)


def assemble_balance(
    facts: CompanyFacts, *, period: StatementPeriod, limit: int
) -> tuple[BalanceSheetRow, ...]:
    series = _series(facts, _BALANCE_FIELDS, period=period, instant=True)
    rows: list[BalanceSheetRow] = []
    for end in _period_ends(series, _BALANCE_CORE, limit):
        values = _values(series, end)
        meta = _meta(series, end)
        rows.append(
            BalanceSheetRow(
                period_end=end,
                fiscal_year=meta.fy if meta else None,
                fiscal_period=meta.fp if meta else None,
                cash=_float(values, "cash"),
                current_assets=_float(values, "current_assets"),
                total_assets=_float(values, "total_assets"),
                current_liabilities=_float(values, "current_liabilities"),
                total_liabilities=_float(values, "total_liabilities"),
                long_term_debt=_float(values, "long_term_debt"),
                stockholders_equity=_float(values, "stockholders_equity"),
                inventory=_float(values, "inventory"),
                accounts_receivable=_float(values, "accounts_receivable"),
            )
        )
    return tuple(rows)


def assemble_cash_flow(
    facts: CompanyFacts, *, period: StatementPeriod, limit: int
) -> tuple[CashFlowRow, ...]:
    series = _series(facts, _CASH_FIELDS, period=period, instant=False)
    rows: list[CashFlowRow] = []
    for end in _period_ends(series, _CASH_CORE, limit):
        values = _values(series, end)
        meta = _meta(series, end)
        rows.append(
            CashFlowRow(
                period_end=end,
                fiscal_year=meta.fy if meta else None,
                fiscal_period=meta.fp if meta else None,
                operating=_float(values, "operating"),
                investing=_float(values, "investing"),
                financing=_float(values, "financing"),
                capex=_float(values, "capex"),
                depreciation=_float(values, "depreciation"),
            )
        )
    return tuple(rows)


def _series(
    facts: CompanyFacts,
    fields: tuple[tuple[str, tuple[str, ...], UnitKind], ...],
    *,
    period: StatementPeriod,
    instant: bool,
) -> tuple[_FieldSeries, ...]:
    return tuple(
        _FieldSeries(name=name, by_end=_points_by_end(facts, tags, period, instant, unit))
        for name, tags, unit in fields
    )


def _points_by_end(
    facts: CompanyFacts,
    tags: tuple[str, ...],
    period: StatementPeriod,
    instant: bool,
    unit: UnitKind,
) -> dict[date, FactPoint]:
    by_end: dict[date, FactPoint] = {}
    for tag in tags:
        best: dict[date, FactPoint] = {}
        concept = facts.concept(tag) or facts.concept(tag, taxonomy="ifrs-full")
        if concept is None:
            continue
        for point in concept.points:
            if point.end is None or not _usable(point, period=period, instant=instant, unit=unit):
                continue
            current = best.get(point.end)
            if current is None or _newer(point, current):
                best[point.end] = point
        for end, point in best.items():
            if end not in by_end:
                by_end[end] = point
    return by_end


def _usable(point: FactPoint, *, period: StatementPeriod, instant: bool, unit: UnitKind) -> bool:
    if not _unit_ok(point.unit, unit):
        return False
    form = (point.form or "").upper().split("/")[0]
    fp = (point.fp or "").upper()
    if period == "annual":
        form_ok = form in _ANNUAL_FORMS or fp == "FY"
    else:
        form_ok = form in _QUARTER_FORMS or fp in {"Q1", "Q2", "Q3"}
    if not form_ok:
        return False
    if instant:
        return point.end is not None
    days = _duration_days(point)
    if days is None:
        return False
    low, high = _ANNUAL_DAYS if period == "annual" else _QUARTER_DAYS
    return low <= days <= high


def _unit_ok(unit: str, kind: UnitKind) -> bool:
    folded = unit.lower().replace(" ", "")
    if kind == "usd":
        return folded == "usd" or (folded.startswith("usd") and "share" not in folded)
    if kind == "per_share":
        return "share" in folded and "usd" in folded
    return "share" in folded and "usd" not in folded


def _duration_days(point: FactPoint) -> int | None:
    if point.start is None or point.end is None:
        return None
    return (point.end - point.start).days


def _newer(left: FactPoint, right: FactPoint) -> bool:
    left_filed = left.filed or date.min
    right_filed = right.filed or date.min
    if left_filed != right_filed:
        return left_filed > right_filed
    return (left.accession or "") > (right.accession or "")


def _period_ends(series: tuple[_FieldSeries, ...], core: tuple[str, ...], limit: int) -> list[date]:
    core_names = set(core)
    ends: set[date] = set()
    for item in series:
        if item.name in core_names:
            ends.update(item.by_end)
    ordered = sorted(ends, reverse=True)
    return ordered[:limit]


def _values(series: tuple[_FieldSeries, ...], end: date) -> Mapping[str, FactPoint]:
    return {item.name: item.by_end[end] for item in series if end in item.by_end}


def _meta(series: tuple[_FieldSeries, ...], end: date) -> FactPoint | None:
    for item in series:
        point = item.by_end.get(end)
        if point is not None:
            return point
    return None


def _float(values: Mapping[str, FactPoint], name: str) -> float | None:
    point = values.get(name)
    return None if point is None else point.value
