"""Phase 0 冒烟测试：应用能构造、健康检查可用、secret 不外泄。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_service.config import get_settings
from agent_service.main import create_app
from agent_service.testing import IsolatedProviderCredentials, IsolatedSettings

_PROVIDER_ENV_VARS = (
    "OPENAI_API_KEY",
    "MOONSHOT_API_KEY",
    "DEEPSEEK_API_KEY",
    "ZHIPU_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "DEFAULT_MODEL_ID",
)


@pytest.fixture
def client() -> TestClient:
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


def test_health_returns_expected_shape(client: TestClient) -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert {p["provider"] for p in body["llm_providers"]} == {
        "openai",
        "moonshot",
        "deepseek",
        "zhipu",
        "anthropic",
        "google",
    }

    # 不需要 key 的数据源应始终报告为可用
    data_sources = {p["provider"]: p["configured"] for p in body["data_sources"]}
    assert data_sources["defillama"] is True
    assert data_sources["sec_edgar"] is True
    assert data_sources["coingecko"] is True


def test_health_never_leaks_secrets(client: TestClient) -> None:
    """健康检查只暴露 configured 布尔值，绝不含 key 本身或其片段（§17.1）。"""
    raw = client.get("/v1/health").text.lower()
    for forbidden in ("sk-", "api_key", "secret", "token"):
        assert forbidden not in raw


@pytest.mark.usefixtures("clean_env")
def test_default_model_id_is_well_formed() -> None:
    """默认模型必须是 `provider:model` 形式且 provider 在已知集合内。

    不断言具体型号：开发期用国内模型、上线切 OpenAI，型号会变（§9.7）。
    """
    provider, _, model = IsolatedSettings().default_model_id.partition(":")
    assert provider in {"openai", "deepseek", "zhipu", "moonshot", "anthropic", "google"}
    assert model


@pytest.mark.usefixtures("clean_env")
def test_execution_limits_are_internally_consistent() -> None:
    limits = IsolatedSettings().limits
    assert limits.max_tasks_per_plan <= 10
    assert limits.max_parallel_tasks <= limits.max_tasks_per_plan
    # 整体超时必须大于单任务超时，否则并行任务永远来不及完成
    assert limits.total_timeout_s > limits.task_timeout_s


def test_secrets_are_not_exposed_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """SecretStr 保证日志与异常里不会意外打印出 key。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-should-not-appear")
    creds = IsolatedProviderCredentials()

    assert creds.openai_api_key is not None
    assert creds.openai_api_key.get_secret_value() == "sk-test-should-not-appear"
    assert "sk-test-should-not-appear" not in repr(creds)
    assert "sk-test-should-not-appear" not in str(creds.openai_api_key)
