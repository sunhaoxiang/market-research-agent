"""web_* tool 的结构化输出（§8.1：T 是 Pydantic 模型，不是 dict）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from agent_service.schemas.common import Schema


class WebSearchHit(Schema):
    url: str
    title: str | None = None
    snippet: str | None = None
    raw_content: str | None = None
    score: float | None = None
    published_at: datetime | None = None
    domain: str | None = None


class WebSearchData(Schema):
    query: str
    hits: list[WebSearchHit]
    topic: str = "general"
    symbols: list[str] = Field(default_factory=list)


class WebPageData(Schema):
    url: str
    final_url: str
    title: str | None = None
    text: str | None = None
    status_code: int
    content_type: str | None = None
