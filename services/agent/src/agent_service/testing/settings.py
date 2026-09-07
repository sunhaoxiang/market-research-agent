"""与本机 env 文件隔离的配置类。

⚠️ 隔离必须逐个类做，不能只设父类。`Settings` 的嵌套字段用
`default_factory` 独立实例化，各自声明了 `env_file`（不声明会导致
.env.local 里的 key 被静默忽略，见 `config.py` 的 `_env_config`）。
父类设 `env_file=None` 管不到它们——漏掉的后果是「没有任何 key」这类
断言在开发机上失败、在 CI 上通过，非常难查。
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from agent_service.config import ExecutionLimits, ProviderCredentials, Settings


def _isolated() -> SettingsConfigDict:
    return SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)


class IsolatedProviderCredentials(ProviderCredentials):
    model_config = _isolated()


class IsolatedExecutionLimits(ExecutionLimits):
    model_config = _isolated()


class IsolatedSettings(Settings):
    """只读 os.environ，不读任何 .env 文件。

    测试里用它 + `monkeypatch.setenv` 精确控制配置，断言才不会因为
    开发机上填了真实 key 而漂移。
    """

    model_config = _isolated()

    providers: ProviderCredentials = Field(default_factory=IsolatedProviderCredentials)
    limits: ExecutionLimits = Field(default_factory=IsolatedExecutionLimits)
