"""Tool 统一契约（§8.1）。

三条关键设计：
  1. **provenance 从 tool 层就产生**，不是报告阶段回补——这是 Citation 系统成立的根本。
  2. **错误是结构化返回值，不抛异常给 LLM**。Agent 看到 `ok=False` + 明确 error code
     可以决定换工具或声明 data gap；抛异常会让 LLM 看到栈信息（噪音 + 潜在信息泄露）。
  3. 泛型 `T` 是具体的 Pydantic 模型，不是 dict。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_service.schemas.common import Schema


class ToolErrorCode(StrEnum):
    """§8.2。UNSUPPORTED / QUOTA_EXHAUSTED 必须与 NOT_FOUND 区分：
    前两者应产生 data gap，后者可能意味着标的名解析错误、值得重试。
    """

    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    """免费额度耗尽（当日/当月）。应快速失败而非等待。"""
    TIMEOUT = "timeout"
    UPSTREAM_ERROR = "upstream_error"
    INVALID_INPUT = "invalid_input"
    UNSUPPORTED = "unsupported"
    """该标的/链不支持此指标。"""
    BLOCKED = "blocked"
    """SSRF 防护或域名黑名单拦截。"""
    PARSE_ERROR = "parse_error"
    """上游返回了非预期结构。"""


class ToolError(Schema):
    code: ToolErrorCode
    message: str = Field(description="面向 LLM 的说明，需可据此决策；不含栈信息")
    tool: str | None = None
    provider: str | None = None
    retryable: bool = False


class DataProvenance(Schema):
    """数据出处。每个成功的 tool 调用都必须产生。"""

    provider: str = Field(description="coingecko / defillama / fmp / sec / tavily")
    endpoint: str
    source_url: str | None = Field(default=None, description="人类可访问的 URL，用于 Citation")
    retrieved_at: datetime = Field(description="抓取时间")
    as_of: datetime | None = Field(default=None, description="数据本身的时点")
    is_cached: bool = False
    cache_age_s: int | None = None


class DataQuality(Schema):
    """数据完整性声明。让"部分成功"可被 Agent 感知，而不是静默当成完整数据。"""

    completeness: Literal["full", "partial"] = "full"
    missing_fields: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list, description='如 "FDV 基于最大供应量估算"')


class ToolResult[T](BaseModel):
    """所有 tool 的统一返回类型。

    不继承 `Schema`：泛型模型需要自己的 model_config，且不应开启
    validate_assignment（泛型解析时会有额外开销）。
    """

    model_config = ConfigDict(extra="ignore")

    ok: bool
    data: T | None = None
    error: ToolError | None = None
    provenance: DataProvenance | None = None
    quality: DataQuality | None = None

    @classmethod
    def success(
        cls,
        data: T,
        provenance: DataProvenance,
        quality: DataQuality | None = None,
    ) -> ToolResult[T]:
        return cls(ok=True, data=data, provenance=provenance, quality=quality)

    @classmethod
    def failure(cls, error: ToolError) -> ToolResult[T]:
        return cls(ok=False, error=error)
