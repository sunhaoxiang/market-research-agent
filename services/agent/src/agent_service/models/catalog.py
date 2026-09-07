"""模型目录（§9.3）。

声明式配置，**新增模型只改这一个文件**。

两条关于数字的纪律：

1. **价格统一折算成 USD**，因为成本护栏 `MAX_SESSION_COST_USD` 是美元。
   国内厂商按人民币报价，用下方 `CNY_PER_USD` 这一个参考汇率换算——
   它只用于成本估算，不追求汇率精度。
2. **每个条目带 `verified_at`**。DeepSeek 在 2026-08 的调价里部分项目涨了 11 倍，
   价格表会过期。`None` 表示该条目的参数尚未对着官方页面核实过，
   成本核算会照算但不应据此做预算决策。
"""

from __future__ import annotations

from datetime import date

from pydantic import Field

from agent_service.models.capabilities import (
    AdapterKind,
    ModelCapabilities,
    PeakSchedule,
    Pricing,
    ProviderId,
    StructuredOutputMode,
    TokenPrices,
)
from agent_service.schemas.common import ModelRole, Schema

CNY_PER_USD = 7.1
"""仅用于把人民币报价折算成美元做成本估算的参考汇率。"""


def _cny(amount: float) -> float:
    """人民币每百万 token 价 → 美元。"""
    return round(amount / CNY_PER_USD, 4)


DEEPSEEK_PEAK = PeakSchedule(
    timezone="Asia/Shanghai",
    weekdays_only=True,
    hour_windows=[(9, 12), (14, 18)],
)
"""DeepSeek 高峰时段：北京时间工作日 09-12、14-18，其余（含周末全天）价格减半。"""


class ModelEntry(Schema):
    id: str = Field(description="我们的稳定 ID，形如 `deepseek:deepseek-v4-pro`")
    provider: ProviderId
    upstream_model: str = Field(description="传给 SDK 的真实模型名")
    adapter: AdapterKind
    display_name: str
    capabilities: ModelCapabilities
    verified_at: date | None = Field(default=None, description="参数最后一次对照官方文档核实的日期")
    notes: str | None = None


_VERIFIED = date(2026, 9, 7)


CATALOG: tuple[ModelEntry, ...] = (
    # ─────────────────────────────────────────────────────────────────────────
    # DeepSeek —— 开发期主力
    # ─────────────────────────────────────────────────────────────────────────
    ModelEntry(
        id="deepseek:deepseek-v4-pro",
        provider=ProviderId.DEEPSEEK,
        upstream_model="deepseek-v4-pro",
        adapter=AdapterKind.OPENAI_CHAT,
        display_name="DeepSeek V4 Pro",
        verified_at=_VERIFIED,
        notes=(
            "开发期 PLANNER / BALANCED / WRITING 的默认模型。"
            "json_mode 已实测确认：发送 response_format=json_schema 会被 400 拒绝，"
            'message 为 "This response_format type is unavailable now"，只支持 json_object。'
            "另需注意它是推理模型，reasoning_content 与正式输出共享 max_tokens 预算，"
            "预算不足时接口返回 200 但 content 为空（见 EmptyOutputError）。"
            "planner 场景实测单次 17-60s，输出 1.1k-4k token，延迟与输出量正相关。"
        ),
        capabilities=ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            structured_output=StructuredOutputMode.JSON_MODE,
            streaming=True,
            reasoning=True,
            vision=False,
            context_window=1_000_000,
            max_output_tokens=384_000,
            pricing=Pricing(
                peak=TokenPrices(input=1.32, output=3.96, cached_input=0.044),
                off_peak=TokenPrices(input=0.66, output=1.98, cached_input=0.022),
                schedule=DEEPSEEK_PEAK,
            ),
        ),
    ),
    ModelEntry(
        id="deepseek:deepseek-v4-flash",
        provider=ProviderId.DEEPSEEK,
        upstream_model="deepseek-v4-flash",
        adapter=AdapterKind.OPENAI_CHAT,
        display_name="DeepSeek V4 Flash",
        verified_at=_VERIFIED,
        notes="开发期 FAST 的默认模型：意图分类与 Web Research。",
        capabilities=ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            structured_output=StructuredOutputMode.JSON_MODE,
            streaming=True,
            reasoning=True,
            vision=False,
            context_window=1_000_000,
            max_output_tokens=384_000,
            pricing=Pricing(
                peak=TokenPrices(input=0.44, output=1.32, cached_input=0.014),
                off_peak=TokenPrices(input=0.22, output=0.66, cached_input=0.007),
                schedule=DEEPSEEK_PEAK,
            ),
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # 智谱 —— 第二 provider，用于验证模型抽象层确实跨 provider 可用
    # ─────────────────────────────────────────────────────────────────────────
    ModelEntry(
        id="zhipu:glm-5.3-flash",
        provider=ProviderId.ZHIPU,
        upstream_model="glm-5.3-flash",
        adapter=AdapterKind.OPENAI_CHAT,
        display_name="GLM-5.3-Flash",
        verified_at=_VERIFIED,
        notes="目录中最便宜的条目（¥0.8/¥2.8）。原生多模态，1M 上下文。",
        capabilities=ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            structured_output=StructuredOutputMode.JSON_MODE,
            streaming=True,
            reasoning=True,
            vision=True,
            context_window=1_000_000,
            max_output_tokens=128_000,
            pricing=Pricing(
                peak=TokenPrices(input=_cny(0.8), output=_cny(2.8), cached_input=_cny(0.23))
            ),
        ),
    ),
    ModelEntry(
        id="zhipu:glm-5.3",
        provider=ProviderId.ZHIPU,
        upstream_model="glm-5.3",
        adapter=AdapterKind.OPENAI_CHAT,
        display_name="GLM-5.3",
        verified_at=_VERIFIED,
        notes="中文写作与长程 Agent 任务表现较强，可作 WRITING 的对比项。",
        capabilities=ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            structured_output=StructuredOutputMode.JSON_MODE,
            streaming=True,
            reasoning=True,
            vision=False,
            context_window=1_000_000,
            max_output_tokens=128_000,
            pricing=Pricing(peak=TokenPrices(input=_cny(8), output=_cny(28), cached_input=_cny(2))),
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # Moonshot —— strict json_schema 的兜底选项（贵，默认不用）
    # ─────────────────────────────────────────────────────────────────────────
    ModelEntry(
        id="moonshot:kimi-k3",
        provider=ProviderId.MOONSHOT,
        upstream_model="kimi-k3",
        adapter=AdapterKind.OPENAI_CHAT,
        display_name="Kimi K3",
        verified_at=_VERIFIED,
        notes=(
            "目录中唯一确认支持 strict json_schema 的非 OpenAI 模型。"
            "输出 $15/M 是 DeepSeek Pro 闲时的 7 倍，仅在 json_mode 路径被证明"
            "不可靠时启用。始终开启推理，用 reasoning_effort 调节。"
        ),
        capabilities=ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            structured_output=StructuredOutputMode.NATIVE_SCHEMA,
            streaming=True,
            reasoning=True,
            vision=True,
            context_window=1_048_576,
            max_output_tokens=131_072,
            pricing=Pricing(peak=TokenPrices(input=3.00, output=15.00, cached_input=0.30)),
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # OpenAI —— 上线期目标。verified_at=None：参数转录自 §9.7，
    # 切换前需对照官方 pricing 页复核（尤其是上下文窗口与缓存价）
    # ─────────────────────────────────────────────────────────────────────────
    ModelEntry(
        id="openai:gpt-5.6-terra",
        provider=ProviderId.OPENAI,
        upstream_model="gpt-5.6-terra",
        adapter=AdapterKind.OPENAI_RESPONSES,
        display_name="GPT-5.6 Terra",
        notes="上线期 BALANCED。上下文窗口为保守估计，切换前需复核。",
        capabilities=ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            structured_output=StructuredOutputMode.NATIVE_SCHEMA,
            streaming=True,
            reasoning=True,
            vision=True,
            context_window=400_000,
            max_output_tokens=128_000,
            pricing=Pricing(peak=TokenPrices(input=2.00, output=12.00)),
        ),
    ),
    ModelEntry(
        id="openai:gpt-5.6-sol",
        provider=ProviderId.OPENAI,
        upstream_model="gpt-5.6-sol",
        adapter=AdapterKind.OPENAI_RESPONSES,
        display_name="GPT-5.6 Sol",
        notes="上线期 PLANNER / WRITING。",
        capabilities=ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            structured_output=StructuredOutputMode.NATIVE_SCHEMA,
            streaming=True,
            reasoning=True,
            vision=True,
            context_window=400_000,
            max_output_tokens=128_000,
            pricing=Pricing(peak=TokenPrices(input=4.00, output=20.00)),
        ),
    ),
    ModelEntry(
        id="openai:gpt-5.6-luna",
        provider=ProviderId.OPENAI,
        upstream_model="gpt-5.6-luna",
        adapter=AdapterKind.OPENAI_RESPONSES,
        display_name="GPT-5.6 Luna",
        notes="上线期 FAST。",
        capabilities=ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            structured_output=StructuredOutputMode.NATIVE_SCHEMA,
            streaming=True,
            reasoning=False,
            vision=True,
            context_window=400_000,
            max_output_tokens=128_000,
            pricing=Pricing(peak=TokenPrices(input=0.20, output=1.20)),
        ),
    ),
    ModelEntry(
        id="openai:gpt-6-astra",
        provider=ProviderId.OPENAI,
        upstream_model="gpt-6-astra",
        adapter=AdapterKind.OPENAI_RESPONSES,
        display_name="GPT-6 Astra",
        notes="可选升档：复杂研究与长财报。",
        capabilities=ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            structured_output=StructuredOutputMode.NATIVE_SCHEMA,
            streaming=True,
            reasoning=True,
            vision=True,
            context_window=1_050_000,
            max_output_tokens=128_000,
            pricing=Pricing(peak=TokenPrices(input=10.00, output=50.00)),
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # Anthropic / Google —— 写入目录但无 key，registry 会自动过滤。
    # 拿到 key 即可用，无需改代码（§9.7）
    # ─────────────────────────────────────────────────────────────────────────
    ModelEntry(
        id="anthropic:claude-sonnet",
        provider=ProviderId.ANTHROPIC,
        upstream_model="anthropic/claude-sonnet-4-5",
        adapter=AdapterKind.LITELLM,
        display_name="Claude Sonnet",
        notes="占位条目，参数未核实。",
        capabilities=ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            structured_output=StructuredOutputMode.PROMPT_ONLY,
            streaming=True,
            reasoning=True,
            vision=True,
            context_window=200_000,
            max_output_tokens=64_000,
        ),
    ),
    ModelEntry(
        id="google:gemini-pro",
        provider=ProviderId.GOOGLE,
        upstream_model="gemini/gemini-2.5-pro",
        adapter=AdapterKind.LITELLM,
        display_name="Gemini Pro",
        notes="占位条目，参数未核实。",
        capabilities=ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            structured_output=StructuredOutputMode.NATIVE_SCHEMA,
            streaming=True,
            reasoning=True,
            vision=True,
            context_window=1_000_000,
            max_output_tokens=64_000,
        ),
    ),
)


ROLE_DEFAULTS: dict[ModelRole, str] = {
    ModelRole.PLANNER: "deepseek:deepseek-v4-pro",
    ModelRole.BALANCED: "deepseek:deepseek-v4-pro",
    ModelRole.FAST: "deepseek:deepseek-v4-flash",
    ModelRole.WRITING: "deepseek:deepseek-v4-pro",
}
"""角色 → 模型的兜底映射（§9.5）。环境变量 `MODEL_ROLE_*` 优先于此。"""


_BY_ID: dict[str, ModelEntry] = {entry.id: entry for entry in CATALOG}


class UnknownModelError(LookupError):
    def __init__(self, model_id: str) -> None:
        known = ", ".join(sorted(_BY_ID))
        super().__init__(f"未知模型 {model_id!r}。目录中可用的是：{known}")
        self.model_id = model_id


def get_entry(model_id: str) -> ModelEntry:
    """按 ID 取目录条目。未知 ID 抛 `UnknownModelError`，不返回 None——
    调用方拿着 None 继续走只会把错误推迟到更难定位的地方。"""
    try:
        return _BY_ID[model_id]
    except KeyError:
        raise UnknownModelError(model_id) from None


def list_entries(provider: ProviderId | None = None) -> tuple[ModelEntry, ...]:
    if provider is None:
        return CATALOG
    return tuple(entry for entry in CATALOG if entry.provider is provider)
