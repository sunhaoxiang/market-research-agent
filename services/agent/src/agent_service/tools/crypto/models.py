"""crypto tool 的结构化输出（§8.1：T 是 Pydantic 模型，不是 dict）。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from agent_service.schemas.common import Schema


class AssetMatchKind(StrEnum):
    EXACT_ID = "exact_id"
    EXACT_SYMBOL = "exact_symbol"
    EXACT_NAME = "exact_name"
    RANKED_SYMBOL = "ranked_symbol"
    UNIQUE_HIT = "unique_hit"
    AMBIGUOUS = "ambiguous"


class ResolvedAsset(Schema):
    coin_id: str
    symbol: str
    name: str
    market_cap_rank: int | None = None
    url: str


class ResolveAssetData(Schema):
    query: str
    match: AssetMatchKind
    resolved: ResolvedAsset | None = None
    candidates: list[ResolvedAsset] = Field(default_factory=list)


class CryptoPriceData(Schema):
    coin_id: str
    vs_currency: str
    price: float
    market_cap: float | None = None
    volume_24h: float | None = None
    change_24h_pct: float | None = None
    as_of: datetime | None = None
    url: str


class CryptoMarketData(Schema):
    coin_id: str
    symbol: str
    name: str
    vs_currency: str
    current_price: float | None = None
    market_cap: float | None = None
    fully_diluted_valuation: float | None = None
    total_volume: float | None = None
    circulating_supply: float | None = None
    total_supply: float | None = None
    max_supply: float | None = None
    ath: float | None = None
    ath_date: datetime | None = None
    atl: float | None = None
    atl_date: datetime | None = None
    high_24h: float | None = None
    low_24h: float | None = None
    change_24h_pct: float | None = None
    last_updated: datetime | None = None
    url: str


class CryptoPricePoint(Schema):
    timestamp: datetime
    price: float


class CryptoPriceHistoryData(Schema):
    coin_id: str
    vs_currency: str
    days: int
    prices: list[CryptoPricePoint] = Field(default_factory=list)
    url: str
