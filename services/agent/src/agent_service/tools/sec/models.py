"""SEC filings / 章节 / XBRL facts 的结构化输出。不解析 JSON，只包 provider 类型。"""

from __future__ import annotations

from datetime import date

from pydantic import Field

from agent_service.schemas.common import Schema


class FilingListItem(Schema):
    accession: str
    form: str
    filed: date | None = None
    report_date: date | None = None
    primary_document: str | None = None
    description: str | None = None
    url: str


class SecFilingsData(Schema):
    ticker: str
    cik: str
    form_types: list[str]
    filings: list[FilingListItem] = Field(default_factory=list)
    url: str


class FilingSectionData(Schema):
    accession: str
    form: str | None = None
    section: str
    heading: str | None = None
    text: str
    truncated: bool = False
    url: str


class XbrlFactPointData(Schema):
    value: float
    unit: str
    end: date | None = None
    start: date | None = None
    filed: date | None = None
    form: str | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    accession: str | None = None


class XbrlConceptData(Schema):
    taxonomy: str
    tag: str
    label: str | None = None
    latest: XbrlFactPointData | None = None
    history: list[XbrlFactPointData] = Field(default_factory=list)


class XbrlFactsData(Schema):
    ticker: str
    cik: str
    concepts: list[XbrlConceptData] = Field(default_factory=list)
    url: str


class EarningsSummaryData(Schema):
    ticker: str
    cik: str
    form: str | None = None
    period_end: date | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    revenue: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    eps_diluted: float | None = None
    accession: str | None = None
    url: str
    latest_8k: FilingListItem | None = None
