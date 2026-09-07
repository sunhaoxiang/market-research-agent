"""P1-4 ModelRegistry 测试。

全部为构造测试，不发出任何真实请求——这也是 P1-4 的验收标准之一。
"""

from __future__ import annotations

import pytest
from agents import OpenAIChatCompletionsModel, OpenAIResponsesModel
from agents.extensions.models.litellm_model import LitellmModel

from agent_service.models.capabilities import ProviderId
from agent_service.models.registry import (
    ModelRegistry,
    ProviderUnavailableError,
    bootstrap_sdk,
)
from agent_service.schemas.common import ModelRole
from agent_service.testing import IsolatedSettings


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "ZHIPU_API_KEY",
        "MOONSHOT_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "MODEL_ROLE_PLANNER",
        "MODEL_ROLE_BALANCED",
        "MODEL_ROLE_FAST",
        "MODEL_ROLE_WRITING",
        "OPENAI_AGENTS_DISABLE_TRACING",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _registry(monkeypatch: pytest.MonkeyPatch, **env: str) -> ModelRegistry:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return ModelRegistry(IsolatedSettings())


# ─── 可用性 ──────────────────────────────────────────────────────────────────


def test_no_keys_means_nothing_is_available(clean_env: pytest.MonkeyPatch) -> None:
    registry = _registry(clean_env)
    assert registry.available_entries() == ()


def test_only_configured_provider_is_available(clean_env: pytest.MonkeyPatch) -> None:
    """只配 1 个 key 时其余显示禁用（P1-5 的前置能力）。"""
    registry = _registry(clean_env, DEEPSEEK_API_KEY="sk-test")

    available = {entry.id for entry in registry.available_entries()}
    assert available == {"deepseek:deepseek-v4-pro", "deepseek:deepseek-v4-flash"}

    reasons = registry.unavailable_reasons()
    assert "deepseek:deepseek-v4-pro" not in reasons
    assert reasons["openai:gpt-5.6-terra"] == "未配置 OPENAI_API_KEY"
    assert reasons["zhipu:glm-5.3-flash"] == "未配置 ZHIPU_API_KEY"


def test_empty_string_key_counts_as_missing(clean_env: pytest.MonkeyPatch) -> None:
    """.env.example 里的空占位值不能被当成有效 key。"""
    registry = _registry(clean_env, DEEPSEEK_API_KEY="")
    assert registry.credential(ProviderId.DEEPSEEK) is None
    assert registry.available_entries() == ()


def test_resolve_without_key_raises_with_actionable_message(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """在 registry 层就报错，而不是等调用 LLM 拿到 401。"""
    registry = _registry(clean_env)
    with pytest.raises(ProviderUnavailableError) as excinfo:
        registry.resolve("deepseek:deepseek-v4-pro")

    message = str(excinfo.value)
    assert "DEEPSEEK_API_KEY" in message
    assert ".env.local" in message


# ─── adapter 构造 ────────────────────────────────────────────────────────────


def test_openai_chat_adapter(clean_env: pytest.MonkeyPatch) -> None:
    registry = _registry(clean_env, DEEPSEEK_API_KEY="sk-test")
    resolved = registry.resolve("deepseek:deepseek-v4-pro")

    assert isinstance(resolved.model, OpenAIChatCompletionsModel)
    assert resolved.entry.upstream_model == "deepseek-v4-pro"
    assert resolved.id == "deepseek:deepseek-v4-pro"


def test_openai_responses_adapter(clean_env: pytest.MonkeyPatch) -> None:
    registry = _registry(clean_env, OPENAI_API_KEY="sk-test")
    resolved = registry.resolve("openai:gpt-5.6-terra")
    assert isinstance(resolved.model, OpenAIResponsesModel)


def test_litellm_adapter(clean_env: pytest.MonkeyPatch) -> None:
    registry = _registry(clean_env, ANTHROPIC_API_KEY="sk-test")
    resolved = registry.resolve("anthropic:claude-sonnet")
    assert isinstance(resolved.model, LitellmModel)


def test_base_url_is_taken_from_settings(clean_env: pytest.MonkeyPatch) -> None:
    """国内 provider 走各自的兼容端点，不能落到 api.openai.com。"""
    registry = _registry(clean_env, DEEPSEEK_API_KEY="sk-test")
    credential = registry.credential(ProviderId.DEEPSEEK)

    assert credential is not None
    assert credential.base_url == "https://api.deepseek.com"


# ─── 缓存 ────────────────────────────────────────────────────────────────────


def test_same_provider_shares_one_client(clean_env: pytest.MonkeyPatch) -> None:
    """同 provider 的多个模型共享 AsyncOpenAI，也就共享 httpx 连接池。"""
    registry = _registry(clean_env, DEEPSEEK_API_KEY="sk-test")

    pro = registry.resolve("deepseek:deepseek-v4-pro")
    flash = registry.resolve("deepseek:deepseek-v4-flash")

    assert isinstance(pro.model, OpenAIChatCompletionsModel)
    assert isinstance(flash.model, OpenAIChatCompletionsModel)
    assert pro.model._client is flash.model._client  # pyright: ignore[reportPrivateUsage]
    assert pro.model is not flash.model


def test_repeated_resolve_returns_same_model_instance(
    clean_env: pytest.MonkeyPatch,
) -> None:
    registry = _registry(clean_env, DEEPSEEK_API_KEY="sk-test")
    assert registry.resolve("deepseek:deepseek-v4-pro").model is (
        registry.resolve("deepseek:deepseek-v4-pro").model
    )


def test_different_providers_get_different_clients(clean_env: pytest.MonkeyPatch) -> None:
    registry = _registry(clean_env, DEEPSEEK_API_KEY="sk-a", ZHIPU_API_KEY="sk-b")

    deepseek = registry.resolve("deepseek:deepseek-v4-pro")
    zhipu = registry.resolve("zhipu:glm-5.3-flash")

    assert isinstance(deepseek.model, OpenAIChatCompletionsModel)
    assert isinstance(zhipu.model, OpenAIChatCompletionsModel)
    assert deepseek.model._client is not zhipu.model._client  # pyright: ignore[reportPrivateUsage]


# ─── 角色映射 ────────────────────────────────────────────────────────────────


def test_roles_fall_back_to_catalog_defaults(clean_env: pytest.MonkeyPatch) -> None:
    registry = _registry(clean_env, DEEPSEEK_API_KEY="sk-test")

    assert registry.model_id_for_role(ModelRole.PLANNER) == "deepseek:deepseek-v4-pro"
    assert registry.model_id_for_role(ModelRole.FAST) == "deepseek:deepseek-v4-flash"


def test_env_override_wins_over_catalog_default(clean_env: pytest.MonkeyPatch) -> None:
    """上线切 OpenAI 只改这几个环境变量，不动代码（§9.7）。"""
    registry = _registry(
        clean_env,
        ZHIPU_API_KEY="sk-test",
        MODEL_ROLE_WRITING="zhipu:glm-5.3",
    )

    assert registry.model_id_for_role(ModelRole.WRITING) == "zhipu:glm-5.3"
    assert registry.for_role(ModelRole.WRITING).entry.display_name == "GLM-5.3"


def test_every_role_resolves_with_a_single_key(clean_env: pytest.MonkeyPatch) -> None:
    """所有角色使用同一 provider 也必须能正常工作——这是 MVP 的默认行为（§9.5）。"""
    registry = _registry(clean_env, DEEPSEEK_API_KEY="sk-test")
    for role in ModelRole:
        assert registry.for_role(role).model is not None


# ─── settings ────────────────────────────────────────────────────────────────


def test_default_settings_enable_usage_reporting(clean_env: pytest.MonkeyPatch) -> None:
    """成本核算依赖 usage，默认必须打开（§20.1）。"""
    registry = _registry(clean_env, DEEPSEEK_API_KEY="sk-test")
    assert registry.resolve("deepseek:deepseek-v4-pro").settings.include_usage is True


def test_parallel_tool_calls_follows_capabilities(clean_env: pytest.MonkeyPatch) -> None:
    registry = _registry(clean_env, DEEPSEEK_API_KEY="sk-test")
    resolved = registry.resolve("deepseek:deepseek-v4-pro")
    assert resolved.settings.parallel_tool_calls is resolved.entry.capabilities.parallel_tool_calls


# ─── bootstrap_sdk ───────────────────────────────────────────────────────────


def test_tracing_disabled_without_openai_key(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert bootstrap_sdk(IsolatedSettings()) is False


def test_tracing_enabled_with_openai_key_even_when_running_other_models(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """核心场景：业务跑 DeepSeek，trace 用 OpenAI key 免费上传（§9.6）。"""
    clean_env.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    clean_env.setenv("OPENAI_API_KEY", "sk-openai")
    assert bootstrap_sdk(IsolatedSettings()) is True


def test_explicit_disable_wins_over_present_key(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("OPENAI_API_KEY", "sk-openai")
    clean_env.setenv("OPENAI_AGENTS_DISABLE_TRACING", "true")
    assert bootstrap_sdk(IsolatedSettings()) is False


def test_bootstrap_does_not_make_openai_the_default_provider(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """OpenAI key 只给 tracing exporter；业务模型仍必须显式解析。"""
    clean_env.setenv("OPENAI_API_KEY", "sk-openai")
    bootstrap_sdk(IsolatedSettings())

    registry = ModelRegistry(IsolatedSettings())
    with pytest.raises(ProviderUnavailableError):
        registry.resolve("deepseek:deepseek-v4-pro")
