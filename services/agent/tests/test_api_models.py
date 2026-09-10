"""P1-5 模型目录端点测试。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from agent_service.config import get_settings
from agent_service.main import create_app

_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "MOONSHOT_API_KEY",
    "DEEPSEEK_API_KEY",
    "ZHIPU_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "MODEL_ROLE_PLANNER",
    "MODEL_ROLE_BALANCED",
    "MODEL_ROLE_FAST",
    "MODEL_ROLE_WRITING",
)


@pytest.fixture
def only_deepseek(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """模拟"只充了 DeepSeek"的开发期状态。"""
    for name in _KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    get_settings.cache_clear()

    yield TestClient(create_app())

    get_settings.cache_clear()


def test_lists_entire_catalog_not_just_available(only_deepseek: TestClient) -> None:
    """不可用的模型也要返回——UI 需要把它们置灰展示，而不是当作不存在。"""
    body = only_deepseek.get("/v1/models").json()
    ids = {m["id"] for m in body["models"]}

    assert "deepseek:deepseek-v4-pro" in ids
    assert "openai:gpt-5.6-terra" in ids
    assert "anthropic:claude-sonnet" in ids


def test_marks_unconfigured_providers_unavailable_with_reason(
    only_deepseek: TestClient,
) -> None:
    models = {m["id"]: m for m in only_deepseek.get("/v1/models").json()["models"]}

    assert models["deepseek:deepseek-v4-pro"]["available"] is True
    assert models["deepseek:deepseek-v4-pro"]["unavailable_reason"] is None

    openai = models["openai:gpt-5.6-terra"]
    assert openai["available"] is False
    assert "OPENAI_API_KEY" in openai["unavailable_reason"]


def test_exposes_capabilities_for_frontend_filtering(only_deepseek: TestClient) -> None:
    models = {m["id"]: m for m in only_deepseek.get("/v1/models").json()["models"]}
    caps = models["deepseek:deepseek-v4-pro"]["capabilities"]

    assert caps["tool_calling"] is True
    assert caps["structured_output"] == "json_mode"
    assert caps["context_window"] == 1_000_000
    assert caps["pricing"]["off_peak"]["input"] == pytest.approx(0.66)


def test_reports_unverified_parameters(only_deepseek: TestClient) -> None:
    """verified_at 为 null 的条目参数未经核实，UI 应据此提示。"""
    models = {m["id"]: m for m in only_deepseek.get("/v1/models").json()["models"]}

    assert models["deepseek:deepseek-v4-pro"]["verified_at"] == "2026-09-07"
    assert models["openai:gpt-5.6-terra"]["verified_at"] is None


def test_reports_smoke_verified_separately_from_pricing(only_deepseek: TestClient) -> None:
    """P5-9：冒烟 `verified` 与价格核实日期不是同一件事。"""
    models = {m["id"]: m for m in only_deepseek.get("/v1/models").json()["models"]}

    assert models["deepseek:deepseek-v4-pro"]["verified"] is True
    assert models["deepseek:deepseek-v4-flash"]["verified"] is True
    assert models["zhipu:glm-5.3-flash"]["verified"] is False
    assert models["zhipu:glm-5.3-flash"]["verified_at"] == "2026-09-07"
    assert models["openai:gpt-5.6-terra"]["verified"] is False


def test_role_defaults_reflect_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("MODEL_ROLE_FAST", "zhipu:glm-5.3-flash")
    get_settings.cache_clear()

    body = TestClient(create_app()).get("/v1/models").json()

    assert body["role_defaults"]["fast"] == "zhipu:glm-5.3-flash"
    assert body["role_defaults"]["balanced"] == "deepseek:deepseek-flash"
    assert body["limits"]["max_tasks_per_plan"] >= 1
    assert body["limits"]["max_parallel_tasks"] >= 1
    get_settings.cache_clear()


def test_never_leaks_key_values(only_deepseek: TestClient) -> None:
    """端点会提到环境变量名（那是有意的提示），但绝不能出现 key 本身。"""
    raw = only_deepseek.get("/v1/models").text
    assert "sk-test" not in raw
