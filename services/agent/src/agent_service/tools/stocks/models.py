"""美股 tool 的结构化输出。P4-3 resolve；P4-4 行情。包 FMP 现成类型，不解析 JSON。"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import Field

from agent_service.schemas.common import Schema


class TickerMatchKind(StrEnum):
    EXACT_TICKER = "exact_ticker"
    EXACT_CIK = "exact_cik"
    EXACT_NAME = "exact_name"
    UNIQUE_NAME = "unique_name"
    AMBIGUOUS = "ambiguous"


class ResolvedTicker(Schema):
    ticker: str
    cik: str
    name: str
    url: str


class ResolveTickerData(Schema):
    query: str
    match: TickerMatchKind
    resolved: ResolvedTicker | None = None
    candidates: list[ResolvedTicker] = Field(default_factory=list)


class StockQuoteData(Schema):
    ticker: str
    name: str | None = None
    price: float
    change: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    day_low: float | None = None
    day_high: float | None = None
    year_low: float | None = None
    year_high: float | None = None
    market_cap: float | None = None
    open: float | None = None
    previous_close: float | None = None
    pe: float | None = None
    eps: float | None = None
    exchange: str | None = None
    as_of: datetime | None = None
    url: str


class StockProfileData(Schema):
    ticker: str
    name: str | None = None
    description: str | None = None
    cik: str | None = None
    exchange: str | None = None
    industry: str | None = None
    sector: str | None = None
    country: str | None = None
    currency: str | None = None
    website: str | None = None
    ceo: str | None = None
    ipo_date: date | None = None
    employees: int | None = None
    market_cap: float | None = None
    beta: float | None = None
    is_etf: bool | None = None
    is_actively_trading: bool | None = None
    url: str


class StockBarData(Schema):
    session: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    volume: float | None = None


class StockPriceHistoryData(Schema):
    ticker: str
    days: int
    start: date
    end: date
    bars: list[StockBarData] = Field(default_factory=list)
    url: str


class StockPeerData(Schema):
    ticker: str
    name: str | None = None
    price: float | None = None
    market_cap: float | None = None
    url: str


class StockPeersData(Schema):
    ticker: str
    peers: list[StockPeerData] = Field(default_factory=list)
    url: str


class IndexCompareData(Schema):
    """同期收益对比。收益是小数（0.15 = 15%），由 Python 按重叠交易日收盘价计算。"""

    ticker: str
    index: str
    days: int
    start: date
    end: date
    n_sessions: int
    ticker_start: float
    ticker_end: float
    index_start: float
    index_end: float
    ticker_return: float
    index_return: float
    excess_return: float
    url: str
