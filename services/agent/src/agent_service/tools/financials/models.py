"""美股三表的结构化输出。包 SEC XBRL / FMP 现成行，不解析 JSON。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from agent_service.schemas.common import Schema

type StatementSource = Literal["sec_xbrl", "fmp"]


class IncomePeriodData(Schema):
    period_end: date
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    revenue: float | None = None
    cost_of_revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    eps_basic: float | None = None
    eps_diluted: float | None = None
    research_and_development: float | None = None
    operating_expenses: float | None = None
    income_tax: float | None = None
    shares_diluted: float | None = None


class IncomeStatementData(Schema):
    ticker: str
    cik: str | None = None
    period: str
    source: StatementSource
    rows: list[IncomePeriodData] = Field(default_factory=list)
    url: str


class BalancePeriodData(Schema):
    period_end: date
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    cash: float | None = None
    current_assets: float | None = None
    total_assets: float | None = None
    current_liabilities: float | None = None
    total_liabilities: float | None = None
    long_term_debt: float | None = None
    stockholders_equity: float | None = None
    inventory: float | None = None
    accounts_receivable: float | None = None


class BalanceSheetData(Schema):
    ticker: str
    cik: str | None = None
    period: str
    source: StatementSource
    rows: list[BalancePeriodData] = Field(default_factory=list)
    url: str


class CashFlowPeriodData(Schema):
    period_end: date
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    operating: float | None = None
    investing: float | None = None
    financing: float | None = None
    capex: float | None = None
    depreciation: float | None = None


class CashFlowData(Schema):
    ticker: str
    cik: str | None = None
    period: str
    source: StatementSource
    rows: list[CashFlowPeriodData] = Field(default_factory=list)
    url: str


class MarginPeriodData(Schema):
    period_end: date
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None


class GrowthMetricsData(Schema):
    ticker: str
    cik: str | None = None
    source: StatementSource
    url: str
    as_of: date | None = None
    cagr_years: float | None = None
    revenue_yoy: float | None = None
    net_income_yoy: float | None = None
    operating_income_yoy: float | None = None
    eps_yoy: float | None = None
    revenue_qoq: float | None = None
    net_income_qoq: float | None = None
    revenue_cagr: float | None = None
    net_income_cagr: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    gross_margin_yoy: float | None = None
    operating_margin_yoy: float | None = None
    net_margin_yoy: float | None = None
    margins: list[MarginPeriodData] = Field(default_factory=list)
