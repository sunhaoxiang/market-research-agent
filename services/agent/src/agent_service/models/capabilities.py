"""模型能力元数据与定价（§9.3）。

能力元数据有三个消费方，缺一不可：
  1. 前端下拉列表（没配 key 或不支持 tool calling 的模型直接禁用，而非调用后报错）
  2. 降级决策（§9.4：structured_output 走哪条路径、要不要关并行工具调用）
  3. 成本计算（§20.1：`agent_runs` 的成本核算与 `MAX_SESSION_COST_USD` 护栏）
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from agent_service.schemas.common import Schema


class ProviderId(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    MOONSHOT = "moonshot"
    DEEPSEEK = "deepseek"
    ZHIPU = "zhipu"


class AdapterKind(StrEnum):
    """底层 `agents.Model` 实现（§9.1）。我们绝不自己写 LLM HTTP 调用。"""

    OPENAI_RESPONSES = "openai_responses"
    """OpenAIResponsesModel：支持 reasoning effort 与 Responses API。"""
    OPENAI_CHAT = "openai_chat"
    """OpenAIChatCompletionsModel：OpenAI 兼容端点（DeepSeek / 智谱 / Moonshot）。"""
    LITELLM = "litellm"
    """LitellmModel：Anthropic / Google 及长尾 provider。"""


class StructuredOutputMode(StrEnum):
    """结构化输出能力档位（§9.4）。三条路径都必须实现并单测覆盖。"""

    NATIVE_SCHEMA = "native_schema"
    """支持 json_schema + strict：模型侧保证字段级约束。"""
    JSON_MODE = "json_mode"
    """仅支持 response_format={"type":"json_object"}：保证合法 JSON，不约束字段。"""
    PROMPT_ONLY = "prompt_only"
    """只能在 prompt 里内嵌 schema，靠代码侧校验兜底。"""


class TokenPrices(Schema):
    """每百万 token 的价格，单位统一为 USD（见 catalog 的换算说明）。"""

    input: float = Field(description="缓存未命中的输入价")
    output: float
    cached_input: float | None = Field(
        default=None,
        description="缓存命中的输入价。通常仅为未命中的 3%–10%，是成本的最大杠杆（§9.8）",
    )


_SATURDAY = 5
"""`datetime.weekday()` 中周六的序号，周日为 6。"""


class PeakSchedule(Schema):
    """分时计价的高峰时段定义。

    DeepSeek 自 2026-08 起按峰谷计价，闲时价格减半。高峰恰好覆盖工作时间，
    因此批量任务（如 Phase 5 的 eval）安排在夜间能直接省一半（§9.7）。
    """

    timezone: str = Field(description="IANA 时区名，如 Asia/Shanghai")
    weekdays_only: bool = True
    hour_windows: list[tuple[int, int]] = Field(
        description="高峰小时区间 [start, end)，如 [(9, 12), (14, 18)]"
    )

    def is_peak(self, at: datetime) -> bool:
        local = at.astimezone(ZoneInfo(self.timezone))
        if self.weekdays_only and local.weekday() >= _SATURDAY:
            return False
        return any(start <= local.hour < end for start, end in self.hour_windows)


class Pricing(Schema):
    """模型定价。分时计价的 provider 额外提供 off_peak。"""

    peak: TokenPrices
    off_peak: TokenPrices | None = None
    schedule: PeakSchedule | None = None

    @model_validator(mode="after")
    def _off_peak_requires_schedule(self) -> Pricing:
        if (self.off_peak is None) != (self.schedule is None):
            msg = "off_peak 与 schedule 必须同时提供：没有时段定义就无法判断该用哪档价"
            raise ValueError(msg)
        return self

    def prices_at(self, at: datetime) -> TokenPrices:
        """返回给定时刻的生效价格。非分时计价的模型恒定返回 peak。"""
        if self.off_peak is None or self.schedule is None:
            return self.peak
        return self.peak if self.schedule.is_peak(at) else self.off_peak

    def cost_usd(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        at: datetime,
    ) -> float:
        """按分时价与缓存命中计算成本。

        `input_tokens` 是**未命中**的输入量，`cached_tokens` 单独计价——
        两者价差可达 30 倍，混在一起算会让成本护栏完全失真（§9.8）。
        """
        prices = self.prices_at(at)
        cached_price = prices.cached_input if prices.cached_input is not None else prices.input
        return (
            input_tokens * prices.input
            + cached_tokens * cached_price
            + output_tokens * prices.output
        ) / 1_000_000


class ModelCapabilities(Schema):
    tool_calling: bool
    parallel_tool_calls: bool
    structured_output: StructuredOutputMode
    streaming: bool
    reasoning: bool
    vision: bool
    context_window: int
    max_output_tokens: int
    pricing: Pricing | None = Field(
        default=None, description="None 表示定价未知，成本核算会跳过该模型并记 warning"
    )
