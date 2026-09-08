"""defi tool 的结构化输出。包 DefiLlama 现成类型，不解析 JSON。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from agent_service.schemas.common import Schema


class TvlPointData(Schema):
    timestamp: datetime
    tvl_usd: float


class ChainTvlShare(Schema):
    chain: str
    tvl_usd: float


class TvlData(Schema):
    scope: Literal["protocol", "chain"]
    protocol: str | None = None
    chain: str | None = None
    name: str | None = None
    symbol: str | None = None
    category: str | None = None
    chains: list[str] = Field(default_factory=list)
    tvl_usd: float | None = None
    chain_tvls: list[ChainTvlShare] = Field(default_factory=list)
    series: list[TvlPointData] = Field(default_factory=list)
    url: str


class FeesRevenueData(Schema):
    protocol: str
    name: str | None = None
    fees_24h: float | None = None
    fees_7d: float | None = None
    fees_30d: float | None = None
    revenue_24h: float | None = None
    revenue_7d: float | None = None
    revenue_30d: float | None = None
    url: str


class DexVolumeData(Schema):
    protocol: str
    name: str | None = None
    volume_24h: float | None = None
    volume_7d: float | None = None
    volume_30d: float | None = None
    volume_all_time: float | None = None
    change_1d: float | None = None
    chains: list[str] = Field(default_factory=list)
    url: str


class ChainOverviewData(Schema):
    name: str
    tvl_usd: float | None = None
    token_symbol: str | None = None
    gecko_id: str | None = None
    chain_id: int | None = None
    url: str
