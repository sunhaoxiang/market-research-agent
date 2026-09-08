"""P5-9 多模型冒烟状态：catalog `verified` + 按 provider 的确定性报告。

不打真实 LLM。CI 与默认 pytest 只核这份报告。
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from agent_service.models import CATALOG, ProviderId, get_entry, list_entries
from agent_service.models.capabilities import StructuredOutputMode
from agent_service.models.smoke import SmokeStatus, provider_smoke_reports
from agent_service.testing import IsolatedProviderCredentials, IsolatedSettings

_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "MOONSHOT_API_KEY",
    "DEEPSEEK_API_KEY",
    "ZHIPU_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
)


@pytest.fixture(autouse=True)
def clear_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """IsolatedSettings 仍读 os.environ；清掉本机/CI 可能残留的 key。"""
    for name in _KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_only_deepseek_entries_are_smoke_verified() -> None:
    verified = {entry.id for entry in CATALOG if entry.verified}
    assert verified == {"deepseek:deepseek-v4-pro", "deepseek:deepseek-v4-flash"}


def test_price_verified_is_not_smoke_verified() -> None:
    """智谱 / Kimi 对过官方价格，但没有完整研究冒烟。"""
    glm = get_entry("zhipu:glm-5.3-flash")
    assert glm.verified_at is not None
    assert glm.verified is False

    kimi = get_entry("moonshot:kimi-k3")
    assert kimi.verified_at is not None
    assert kimi.verified is False


def test_deepseek_smoke_uses_json_mode() -> None:
    """§9.4：开发期 DeepSeek 走 json_mode，冒烟通过不等于 native_schema 可用。"""
    for entry in list_entries(ProviderId.DEEPSEEK):
        assert entry.verified is True
        assert entry.capabilities.structured_output is StructuredOutputMode.JSON_MODE


def test_unverified_native_schema_and_prompt_only_are_labelled() -> None:
    """OpenAI / Kimi 的 native_schema、Anthropic 的 prompt_only 本机都没 live。"""
    openai = get_entry("openai:gpt-5.6-terra")
    assert openai.capabilities.structured_output is StructuredOutputMode.NATIVE_SCHEMA
    assert openai.verified is False
    assert "OPENAI_API_KEY" in (openai.notes or "")

    kimi = get_entry("moonshot:kimi-k3")
    assert kimi.capabilities.structured_output is StructuredOutputMode.NATIVE_SCHEMA
    assert kimi.verified is False
    assert "MOONSHOT_API_KEY" in (kimi.notes or "")

    claude = get_entry("anthropic:claude-sonnet")
    assert claude.capabilities.structured_output is StructuredOutputMode.PROMPT_ONLY
    assert claude.verified is False
    assert "ANTHROPIC_API_KEY" in (claude.notes or "")


def test_only_deepseek_key_marks_others_skipped() -> None:
    settings = IsolatedSettings(
        providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
    )
    reports = {item.provider: item for item in provider_smoke_reports(settings)}

    assert set(reports) == set(ProviderId)

    deepseek = reports[ProviderId.DEEPSEEK]
    assert deepseek.status is SmokeStatus.VERIFIED
    assert deepseek.verified_model_ids == [
        "deepseek:deepseek-v4-pro",
        "deepseek:deepseek-v4-flash",
    ]
    assert StructuredOutputMode.JSON_MODE in deepseek.structured_output
    assert "json_mode" in deepseek.note
    assert "planner" in deepseek.note

    skipped = [item for item in reports.values() if item.provider is not ProviderId.DEEPSEEK]
    assert skipped
    assert all(item.status is SmokeStatus.SKIPPED_NO_KEY for item in skipped)
    assert all(not item.verified_model_ids for item in skipped)
    assert "OPENAI_API_KEY" in reports[ProviderId.OPENAI].note
    assert "ZHIPU_API_KEY" in reports[ProviderId.ZHIPU].note


def test_configured_but_unverified_provider_is_pending() -> None:
    """配了智谱 key、catalog 还没标 verified → pending，而不是假装跑过。"""
    settings = IsolatedSettings(
        providers=IsolatedProviderCredentials(zhipu_api_key=SecretStr("sk-zhipu"))
    )
    reports = {item.provider: item for item in provider_smoke_reports(settings)}

    zhipu = reports[ProviderId.ZHIPU]
    assert zhipu.status is SmokeStatus.PENDING
    assert zhipu.verified_model_ids == []
    assert "ZHIPU_API_KEY" in zhipu.note
    assert "尚未完成" in zhipu.note

    assert reports[ProviderId.DEEPSEEK].status is SmokeStatus.SKIPPED_NO_KEY
