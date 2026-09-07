"""来源（§15）。

核心设计：**Source 由 tool 层自动产生，不依赖 LLM 自觉**（§15.1）。
LLM 只输出短引用（`s1`/`s2`），由编排层映射到真实 Source——因为让 LLM
复述 URL 是已知的幻觉高发点。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from agent_service.schemas.common import Schema, SourceReliability, SourceType
from agent_service.utils.ids import new_id


class Source(Schema):
    """一个可引用的信息来源。

    API 类数据源也是 Source（provider + endpoint + 人类可访问 URL），
    这样"HYPE TVL 增长 18%"这种来自 API 的数字同样可溯源。
    """

    id: str = Field(default_factory=new_id)
    ref: str = Field(description="会话内短引用，如 s1。LLM 在 claim 中用它指代本来源")
    url: str
    url_canonical: str = Field(description="归一化 URL，用于跨 Agent 去重（§15.1）")
    title: str | None = None
    domain: str | None = None
    source_type: SourceType
    provider: str | None = Field(default=None, description="coingecko / defillama / tavily …")
    reliability: SourceReliability = SourceReliability.UNKNOWN
    published_at: datetime | None = None
    retrieved_at: datetime
    excerpt: str | None = Field(
        default=None, description="支撑证据片段，供 UI 悬浮预览与引用有效性校验"
    )
    citation_index: int | None = Field(
        default=None, description="报告中的引用序号 [n]。未被引用的来源为 None（§15.4-3）"
    )
    http_status: int | None = Field(
        default=None, description="抓取时的 HTTP 状态，404 标记为不可用"
    )
