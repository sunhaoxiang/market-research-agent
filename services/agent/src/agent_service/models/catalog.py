"""模型目录（§9.3）。

声明式配置，**新增模型只改这一个文件**。

三条关于数字与冒烟的纪律：

1. **价格统一折算成 USD**，因为成本护栏 `MAX_SESSION_COST_USD` 是美元。
   国内厂商按人民币报价，用下方 `CNY_PER_USD` 这一个参考汇率换算——
   它只用于成本估算，不追求汇率精度。
2. **每个条目带 `verified_at`**。DeepSeek 在 2026-08 的调价里部分项目涨了 11 倍，
   价格表会过期。`None` 表示该条目的参数尚未对着官方页面核实过，
   成本核算会照算但不应据此做预算决策。
3. **`verified` 是 P5-9 完整研究冒烟结果**，与 `verified_at` 不是同一件事。
   价格对过官方文档 ≠ 这条模型跑通过一次研究。没配 key 的 provider
   保持 `verified=False`，并在 notes 里写明限制。
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


def _half_price(prices: TokenPrices) -> TokenPrices:
    """闲时价 = 高峰的一半。用除法而不是再 round(_cny)，避免 0.1408 vs 0.14085。"""
    cached = None if prices.cached_input is None else prices.cached_input / 2
    return TokenPrices(input=prices.input / 2, output=prices.output / 2, cached_input=cached)


DEEPSEEK_PEAK = PeakSchedule(
    timezone="Asia/Shanghai",
    weekdays_only=True,
    hour_windows=[(9, 12), (14, 18)],
)
"""DeepSeek 高峰时段：北京时间工作日 09-12、14-18，其余（含周末全天）价格减半。"""

_FLASH_PEAK = TokenPrices(input=_cny(2), output=_cny(8), cached_input=_cny(0.04))
_FLASH_PRICING = Pricing(
    peak=_FLASH_PEAK,
    off_peak=_half_price(_FLASH_PEAK),
    schedule=DEEPSEEK_PEAK,
)
"""V4.1 Flash / 已路由的 Flash 系列：官方 2026-09-10 调价（¥/MTok，闲时半价）。"""


class ModelEntry(Schema):
    id: str = Field(description="我们的稳定 ID，形如 `deepseek:deepseek-v4-pro`")
    provider: ProviderId
    upstream_model: str = Field(description="传给 SDK 的真实模型名")
    adapter: AdapterKind
    display_name: str
    capabilities: ModelCapabilities
    verified_at: date | None = Field(default=None, description="参数最后一次对照官方文档核实的日期")
    verified: bool = Field(
        default=False,
        description=(
            "P5-9：是否完成过一次完整研究冒烟（含 §9.4 结构化输出路径）。"
            "与 verified_at 不是同一件事——后者只说明价格表对过官方文档。"
        ),
    )
    notes: str | None = None


_VERIFIED = date(2026, 9, 7)
_VERIFIED_FLASH = date(2026, 9, 10)


CATALOG: tuple[ModelEntry, ...] = (
    # ─────────────────────────────────────────────────────────────────────────
    # DeepSeek —— 开发期主力
    # ─────────────────────────────────────────────────────────────────────────
    ModelEntry(
        id="deepseek:deepseek-flash",
        provider=ProviderId.DEEPSEEK,
        upstream_model="deepseek-flash",
        adapter=AdapterKind.OPENAI_CHAT,
        display_name="DeepSeek V4.1 Flash",
        verified_at=_VERIFIED_FLASH,
        verified=True,
        notes=(
            "2026-09-10 上线的默认模型。官方 API 名 `deepseek-flash`；"
            "性能、费用、速度宣称全面超过 V4 Pro，"
            "四角色（PLANNER / BALANCED / FAST / WRITING）均指向它。"
            "原生多模态、1M 上下文；结构化输出仍按 json_mode"
            "（与其它 DeepSeek 条目相同）。"
            "json_mode 路径已在 V4 Pro / V4 Flash 上冒烟通过；"
            "本条目沿用同一适配器，未单独重跑完整研究。"
        ),
        capabilities=ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            structured_output=StructuredOutputMode.JSON_MODE,
            streaming=True,
            reasoning=True,
            vision=True,
            context_window=1_000_000,
            max_output_tokens=384_000,
            pricing=_FLASH_PRICING,
        ),
    ),
    ModelEntry(
        id="deepseek:deepseek-v4-pro",
        provider=ProviderId.DEEPSEEK,
        upstream_model="deepseek-v4-pro",
        adapter=AdapterKind.OPENAI_CHAT,
        display_name="DeepSeek V4 Pro",
        verified_at=_VERIFIED,
        verified=True,
        notes=(
            "兼容条目，不再作为角色默认。"
            "json_mode 已实测确认：发送 response_format=json_schema 会被 400 拒绝，"
            'message 为 "This response_format type is unavailable now"，只支持 json_object。'
            "偶发返回 200 + 空 content（见 EmptyOutputError），已按可重试处理。"
            "P5-9（2026-09-08）：完整研究冒烟通过（Phase 2–4 真跑，json_mode）。"
            "官方：北京时间 2026-09-14 12:00 起，"
            "deepseek-v4-pro 将路由到 V4.1 Flash 并按 Flash 单价计费。"
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
        verified_at=_VERIFIED_FLASH,
        verified=True,
        notes=(
            "兼容条目。官方已下线独立服务，deepseek-v4-flash 暂时路由到 V4.1 Flash，"
            "按 Flash 系列 2026-09-10 单价计费。新调用请用 `deepseek:deepseek-flash`。"
            "P5-9：同一真跑会话的 FAST 角色已覆盖。"
        ),
        capabilities=ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            structured_output=StructuredOutputMode.JSON_MODE,
            streaming=True,
            reasoning=True,
            vision=True,
            context_window=1_000_000,
            max_output_tokens=384_000,
            pricing=_FLASH_PRICING,
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
        notes=(
            "目录中最便宜的条目（¥0.8/¥2.8）。原生多模态，1M 上下文。"
            "P5-9：未配置 ZHIPU_API_KEY，完整研究冒烟未跑。"
        ),
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
        notes=(
            "中文写作与长程 Agent 任务表现较强，可作 WRITING 的对比项。"
            "P5-9：未配置 ZHIPU_API_KEY，完整研究冒烟未跑。"
        ),
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
            "P5-9：未配置 MOONSHOT_API_KEY，native_schema 完整研究冒烟未跑。"
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
        notes=(
            "上线期 BALANCED。上下文窗口为保守估计，切换前需复核。"
            "P5-9：未配置 OPENAI_API_KEY，native_schema 完整研究冒烟未跑。"
        ),
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
        notes=("上线期 PLANNER / WRITING。P5-9：未配置 OPENAI_API_KEY，完整研究冒烟未跑。"),
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
        notes=("上线期 FAST。P5-9：未配置 OPENAI_API_KEY，完整研究冒烟未跑。"),
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
        notes=("可选升档：复杂研究与长财报。P5-9：未配置 OPENAI_API_KEY，完整研究冒烟未跑。"),
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
        notes=(
            "占位条目，参数未核实。P5-9：未配置 ANTHROPIC_API_KEY，prompt_only 完整研究冒烟未跑。"
        ),
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
        notes=("占位条目，参数未核实。P5-9：未配置 GOOGLE_API_KEY，完整研究冒烟未跑。"),
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
    ModelRole.PLANNER: "deepseek:deepseek-flash",
    ModelRole.BALANCED: "deepseek:deepseek-flash",
    ModelRole.FAST: "deepseek:deepseek-flash",
    ModelRole.WRITING: "deepseek:deepseek-flash",
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
