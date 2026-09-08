"""美股 tool 的结构化输出。P4-3 先做 resolve_ticker；行情 tools 是 P4-4。"""

from __future__ import annotations

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
