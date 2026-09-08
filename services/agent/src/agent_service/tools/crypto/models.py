"""resolve_asset 的结构化输出（§8.1：T 是 Pydantic 模型，不是 dict）。"""

from __future__ import annotations

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
