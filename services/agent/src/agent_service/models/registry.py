"""模型注册表（§9.1 / §9.2）。

职责边界：`ModelRegistry` 只负责「凭证管理 + 能力元数据 + 角色映射」，
**底层 `Model` 实现全部复用 SDK**（`OpenAIResponsesModel` / `OpenAIChatCompletionsModel` /
`LitellmModel`），绝不自己写 LLM HTTP 调用。

为什么不直接用 SDK 的 `MultiProvider`（§9.2）：它只从环境变量读 key 与 base_url，
且不提供能力元数据——而我们需要用能力元数据驱动前端下拉列表、结构化输出降级决策
和成本计算这三件事。

**不依赖任何 SDK 全局状态**：每个 `Model` 都显式持有自己的客户端，
不调用 `set_default_openai_key` 之类的全局设置。这样同一进程内可以同时用
DeepSeek 跑业务、用 OpenAI key 传 trace，两者互不干扰。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from agents import (
    Model,
    ModelSettings,
    OpenAIChatCompletionsModel,
    OpenAIResponsesModel,
    set_tracing_disabled,
    set_tracing_export_api_key,
)
from agents.extensions.models.litellm_model import LitellmModel
from openai import AsyncOpenAI

from agent_service.config import Settings, get_settings
from agent_service.models.capabilities import AdapterKind, ProviderId
from agent_service.models.catalog import CATALOG, ROLE_DEFAULTS, ModelEntry, get_entry
from agent_service.schemas.common import ModelRole

if TYPE_CHECKING:
    from pydantic import SecretStr

log = structlog.get_logger(__name__)


class ProviderUnavailableError(RuntimeError):
    """选中的模型所属 provider 没有配置 API key。

    在 registry 层就抛出，而不是等调用 LLM 时拿到 401——后者的报错信息
    对使用者毫无帮助（§9.3：没配 key 的 provider 应在 UI 中禁用并提示原因）。
    """

    def __init__(self, provider: ProviderId, model_id: str) -> None:
        env_var = f"{provider.value.upper()}_API_KEY"
        super().__init__(
            f"模型 {model_id!r} 所属的 provider {provider.value!r} 未配置 API key。"
            f"请在 .env.local 中设置 {env_var}"
        )
        self.provider = provider
        self.model_id = model_id


@dataclass(frozen=True)
class ProviderCredential:
    api_key: str
    base_url: str | None


@dataclass(frozen=True)
class ResolvedModel:
    """交给 Agent 的完整模型描述。"""

    entry: ModelEntry
    model: Model
    settings: ModelSettings

    @property
    def id(self) -> str:
        return self.entry.id


def _secret(value: SecretStr | None) -> str | None:
    text = value.get_secret_value() if value is not None else None
    return text or None


class ModelRegistry:
    """按需构造并缓存 `Model` 实例。

    客户端按 provider 缓存而非按模型缓存：同一 provider 的多个模型共享
    一个 `AsyncOpenAI`，也就共享底层 httpx 连接池。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._clients: dict[ProviderId, AsyncOpenAI] = {}
        self._models: dict[str, Model] = {}

    @property
    def settings(self) -> Settings:
        return self._settings

    # ── 凭证 ────────────────────────────────────────────────────────────────

    def credential(self, provider: ProviderId) -> ProviderCredential | None:
        """返回 provider 的凭证，未配置 key 时返回 None。"""
        creds = self._settings.providers
        table: dict[ProviderId, tuple[str | None, str | None]] = {
            ProviderId.OPENAI: (_secret(creds.openai_api_key), creds.openai_base_url),
            ProviderId.DEEPSEEK: (_secret(creds.deepseek_api_key), creds.deepseek_base_url),
            ProviderId.ZHIPU: (_secret(creds.zhipu_api_key), creds.zhipu_base_url),
            ProviderId.MOONSHOT: (_secret(creds.moonshot_api_key), creds.moonshot_base_url),
            ProviderId.ANTHROPIC: (_secret(creds.anthropic_api_key), None),
            ProviderId.GOOGLE: (_secret(creds.google_api_key), None),
        }
        api_key, base_url = table[provider]
        if api_key is None:
            return None
        return ProviderCredential(api_key=api_key, base_url=base_url)

    def is_available(self, model_id: str) -> bool:
        return self.credential(get_entry(model_id).provider) is not None

    def available_entries(self) -> tuple[ModelEntry, ...]:
        """目录中当前可用（已配置 key）的条目。驱动 `GET /v1/models`。"""
        return tuple(entry for entry in CATALOG if self.is_available(entry.id))

    def unavailable_reasons(self) -> dict[str, str]:
        """model_id → 不可用原因。前端据此把选项置灰并显示提示。"""
        return {
            entry.id: f"未配置 {entry.provider.value.upper()}_API_KEY"
            for entry in CATALOG
            if not self.is_available(entry.id)
        }

    # ── 解析 ────────────────────────────────────────────────────────────────

    def resolve(self, model_id: str) -> ResolvedModel:
        """按 ID 解析出可直接交给 `Agent(model=...)` 的对象。"""
        entry = get_entry(model_id)
        credential = self.credential(entry.provider)
        if credential is None:
            raise ProviderUnavailableError(entry.provider, entry.id)

        model = self._models.get(entry.id)
        if model is None:
            model = self._build_model(entry, credential)
            self._models[entry.id] = model

        return ResolvedModel(entry=entry, model=model, settings=self._default_settings(entry))

    def for_role(self, role: ModelRole) -> ResolvedModel:
        """按角色解析。优先级：`MODEL_ROLE_*` 环境变量 > 目录兜底映射（§9.5）。"""
        return self.resolve(self.model_id_for_role(role))

    def model_id_for_role(self, role: ModelRole) -> str:
        overrides: dict[ModelRole, str | None] = {
            ModelRole.PLANNER: self._settings.model_role_planner,
            ModelRole.BALANCED: self._settings.model_role_balanced,
            ModelRole.FAST: self._settings.model_role_fast,
            ModelRole.WRITING: self._settings.model_role_writing,
        }
        return overrides[role] or ROLE_DEFAULTS[role]

    # ── 构造 ────────────────────────────────────────────────────────────────

    def _client(self, provider: ProviderId, credential: ProviderCredential) -> AsyncOpenAI:
        client = self._clients.get(provider)
        if client is None:
            client = AsyncOpenAI(api_key=credential.api_key, base_url=credential.base_url)
            self._clients[provider] = client
        return client

    def _build_model(self, entry: ModelEntry, credential: ProviderCredential) -> Model:
        match entry.adapter:
            case AdapterKind.OPENAI_RESPONSES:
                return OpenAIResponsesModel(
                    model=entry.upstream_model,
                    openai_client=self._client(entry.provider, credential),
                )
            case AdapterKind.OPENAI_CHAT:
                return OpenAIChatCompletionsModel(
                    model=entry.upstream_model,
                    openai_client=self._client(entry.provider, credential),
                )
            case AdapterKind.LITELLM:
                # LitellmModel 自己管理 HTTP 客户端，因此不走 _client 缓存
                return LitellmModel(
                    model=entry.upstream_model,
                    api_key=credential.api_key,
                    base_url=credential.base_url,
                )

    def _default_settings(self, entry: ModelEntry) -> ModelSettings:
        """按能力生成默认 settings（§9.4 的降级策略之一）。"""
        return ModelSettings(
            parallel_tool_calls=entry.capabilities.parallel_tool_calls,
            include_usage=True,  # 成本核算依赖 usage（§20.1）
        )

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
        self._models.clear()


@dataclass(frozen=True)
class TracingStatus:
    enabled: bool
    reason: str


def tracing_status(settings: Settings | None = None) -> TracingStatus:
    """判断 tracing 是否应当启用。纯函数，无副作用。

    与 `bootstrap_sdk()` 共用，好处是 `/v1/health` 报告的状态与实际生效的状态
    不可能漂移——否则健康检查会在没有 OpenAI key 时依然宣称 tracing 已开启。
    """
    settings = settings or get_settings()

    if settings.openai_agents_disable_tracing:
        return TracingStatus(enabled=False, reason="配置显式关闭")
    if _secret(settings.providers.openai_api_key) is None:
        return TracingStatus(enabled=False, reason="未配置 OPENAI_API_KEY")
    return TracingStatus(enabled=True, reason="已启用，trace 上传到 OpenAI Traces 面板")


def bootstrap_sdk(settings: Settings | None = None) -> bool:
    """配置 SDK 的全局 tracing 开关。返回 tracing 是否启用。

    tracing 导出与业务模型调用是两条独立通道（§9.6）：即使业务跑在 DeepSeek 上，
    只要提供一个 OpenAI key 就能把 trace 上传到 Traces 面板，且**不计费**。
    因此开发期充 $5 OpenAI key 只为买回这个调试面是划算的。

    没有 OpenAI key 时必须显式关闭，否则每次运行都会尝试上传并报错。
    """
    settings = settings or get_settings()
    status = tracing_status(settings)

    if not status.enabled:
        set_tracing_disabled(True)
        log.warning(
            "sdk.tracing.disabled",
            reason=status.reason,
            impact="自建埋点（research_events / tool_calls / agent_runs）成为唯一调试面",
        )
        return False

    openai_key = _secret(settings.providers.openai_api_key)
    assert openai_key is not None  # noqa: S101 — tracing_status 已保证

    # 只把 key 给 tracing exporter，不设为默认模型 key——
    # 后者会让 SDK 在业务调用时误用 OpenAI 客户端
    set_tracing_export_api_key(openai_key)
    set_tracing_disabled(False)
    log.info("sdk.tracing.enabled", exporter="openai")
    return True
