"""配置加载测试。

存在的理由：嵌套 BaseSettings 不继承父类的 `env_file`，一旦漏配就会
**静默**忽略 .env.local 里的 API key——表现为 key 明明填了却读不到，
且没有任何报错。这个坑真实发生过一次，用测试锁住。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_settings import BaseSettings

from agent_service.config import (
    ENV_FILES,
    ExecutionLimits,
    ProviderCredentials,
    Settings,
    apply_limit_overrides,
)
from agent_service.testing import (
    IsolatedExecutionLimits,
    IsolatedProviderCredentials,
    IsolatedSettings,
)


@pytest.mark.parametrize("cls", [Settings, ProviderCredentials, ExecutionLimits])
def test_every_settings_class_reads_the_env_files(cls: type[BaseSettings]) -> None:
    """嵌套配置类必须各自声明 env_file，否则只会读 os.environ。"""
    assert cls.model_config.get("env_file") == ENV_FILES


@pytest.mark.parametrize(
    "cls", [IsolatedSettings, IsolatedProviderCredentials, IsolatedExecutionLimits]
)
def test_isolated_variants_read_no_env_file(cls: type[BaseSettings]) -> None:
    """测试用的隔离版本必须逐个覆盖，漏一个就会让「无 key」类断言
    在开发机上失败、在 CI 上通过——这类差异极难定位。"""
    assert cls.model_config.get("env_file") is None


def test_isolated_settings_isolates_its_nested_fields() -> None:
    """光把父类设成 env_file=None 不够：嵌套字段由 default_factory
    独立实例化，必须换成隔离版本才不会读到本机的真实 key。"""
    settings = IsolatedSettings()

    assert type(settings.providers).model_config.get("env_file") is None
    assert type(settings.limits).model_config.get("env_file") is None


def test_nested_credentials_actually_load_from_a_dotenv_file(tmp_path: Path) -> None:
    """结构断言不够——真正读一遍文件，确认 key 能落到嵌套模型里。"""
    env_file = tmp_path / ".env.test"
    env_file.write_text("DEEPSEEK_API_KEY=sk-from-file\n", encoding="utf-8")

    creds = ProviderCredentials(_env_file=env_file)  # pyright: ignore[reportCallIssue]

    assert creds.deepseek_api_key is not None
    assert creds.deepseek_api_key.get_secret_value() == "sk-from-file"


def test_env_files_prefer_local_over_shared() -> None:
    """.env.local 放真实 key 且已 gitignore，必须优先于 .env（§17.1）。"""
    assert [path.name for path in ENV_FILES] == [".env.local", ".env"]


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_keys_normalize_to_none(tmp_path: Path, blank: str) -> None:
    """`.env` 里未填写的行是 `OPENAI_API_KEY=`，解析后是 SecretStr("")。

    若不归一化，所有 `key is not None` 的判断都会认为该 provider 已配置——
    /v1/health 曾因此把 6 个 provider 全报成 configured，而实际只有 1 个有 key。
    """
    env_file = tmp_path / ".env.test"
    env_file.write_text(f"OPENAI_API_KEY={blank}\nDEEPSEEK_API_KEY=sk-real\n", encoding="utf-8")

    creds = IsolatedProviderCredentials(_env_file=env_file)  # pyright: ignore[reportCallIssue]

    assert creds.openai_api_key is None
    assert creds.deepseek_api_key is not None


def test_blank_model_role_normalizes_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """空串会让 registry 去解析一个空模型 ID，报错信息极难理解。"""
    monkeypatch.setenv("MODEL_ROLE_PLANNER", "")
    assert IsolatedSettings().model_role_planner is None


def test_secrets_are_masked_in_repr(tmp_path: Path) -> None:
    """key 绝不能因为一次 print/日志就泄露（§17.1）。"""
    env_file = tmp_path / ".env.test"
    env_file.write_text("DEEPSEEK_API_KEY=sk-super-secret\n", encoding="utf-8")

    creds = ProviderCredentials(_env_file=env_file)  # pyright: ignore[reportCallIssue]

    assert "sk-super-secret" not in repr(creds)
    assert "sk-super-secret" not in str(creds)


def test_apply_limit_overrides_only_patches_provided_fields() -> None:
    base = IsolatedExecutionLimits()
    patched = apply_limit_overrides(base, {"max_tasks_per_plan": 3})

    assert patched.max_tasks_per_plan == 3
    assert patched.max_parallel_tasks == base.max_parallel_tasks
    assert apply_limit_overrides(base, None) is base
