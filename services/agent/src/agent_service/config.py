"""集中式配置。所有环境变量只在此处读取，业务代码通过 get_settings() 获取。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根目录：src/agent_service/config.py → 上溯 4 层
REPO_ROOT = Path(__file__).resolve().parents[4]

# .env.local 优先于 .env（前者放真实 key 且已 gitignore，见 §17.1）
ENV_FILES = (REPO_ROOT / ".env.local", REPO_ROOT / ".env")


def _blank_to_none(value: object) -> object:
    """把空白字符串归一化为 None。

    .env 文件里未填写的行长这样：`OPENAI_API_KEY=`。pydantic 会把它解析成
    `SecretStr("")`，而 `"" is not None` 为真——于是所有 `key is not None`
    的判断都会认为该 provider 已配置，健康检查会在**最需要它准确的时候**
    谎报可用。在这里统一处理，下游就不可能再踩。
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


type OptionalSecret = Annotated[SecretStr | None, BeforeValidator(_blank_to_none)]
"""可选的 secret：未配置与填了空串都归一为 None。"""

type OptionalText = Annotated[str | None, BeforeValidator(_blank_to_none)]
"""可选的文本配置：空串归一为 None，避免下游把 "" 当成有效值去解析。"""


def _env_config() -> SettingsConfigDict:
    """所有配置类共用的 env 读取规则。

    ⚠️ 嵌套的 BaseSettings **不会**继承父类的 `env_file`——它由
    `default_factory` 独立实例化，届时只读 `os.environ`。因此
    `ProviderCredentials` / `ExecutionLimits` 必须各自声明 env_file，
    否则 .env.local 里的 API key 会被静默忽略（表现为 key 明明填了却读不到）。
    """
    return SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


class ProviderCredentials(BaseSettings):
    """LLM provider 的 key 与 base_url。缺失的 provider 在 ModelRegistry 层被标记为不可用。"""

    model_config = _env_config()

    # OpenAI 为首选 provider；该 key 同时用于 SDK tracing 上传（见 §9.6）
    openai_api_key: OptionalSecret = None
    moonshot_api_key: OptionalSecret = None
    deepseek_api_key: OptionalSecret = None
    zhipu_api_key: OptionalSecret = None
    anthropic_api_key: OptionalSecret = None
    google_api_key: OptionalSecret = None

    openai_base_url: OptionalText = None
    moonshot_base_url: str = "https://api.moonshot.cn/v1"
    deepseek_base_url: str = "https://api.deepseek.com"
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"


class ExecutionLimits(BaseSettings):
    """研究流程的硬上限，防止成本与延迟失控（DEVELOPMENT_PLAN.md §7.2）。"""

    model_config = _env_config()

    max_tasks_per_plan: int = Field(default=6, ge=1, le=10)
    max_parallel_tasks: int = Field(default=2, ge=1, le=6)
    max_tool_calls_per_agent: int = Field(default=12, ge=1, le=20)
    max_supplement_rounds: int = Field(default=1, ge=0, le=2)
    task_timeout_s: float = Field(default=180.0, gt=0)
    total_timeout_s: float = Field(default=600.0, gt=0)
    max_session_cost_usd: float = Field(default=1.0, gt=0)


class Settings(BaseSettings):
    """服务全局配置。"""

    model_config = _env_config()

    # ── 服务
    agent_service_host: str = "127.0.0.1"
    agent_service_port: int = 8000
    internal_api_token: OptionalSecret = None
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # ── 数据源
    tavily_api_key: OptionalSecret = None
    coingecko_api_key: OptionalSecret = None
    fmp_api_key: OptionalSecret = None
    sec_user_agent: str = "market-research-agent contact@example.com"

    # ── 缓存
    agent_cache_db: Path = REPO_ROOT / "data" / "provider-cache.db"

    # ── 模型（见 §9.5 / §9.7）。开发期用国内模型，上线切 OpenAI 只改环境变量
    default_model_id: str = "deepseek:deepseek-v4-pro"
    model_role_planner: OptionalText = None
    model_role_balanced: OptionalText = None
    model_role_fast: OptionalText = None
    model_role_writing: OptionalText = None

    # 有 OPENAI_API_KEY 时默认开启 SDK tracing；无 key 时 bootstrap_sdk() 会强制关闭
    openai_agents_disable_tracing: bool = False

    # ── 嵌套配置
    providers: ProviderCredentials = Field(default_factory=ProviderCredentials)
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)

    @property
    def cache_db_path(self) -> Path:
        path = self.agent_cache_db
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例。测试中用 get_settings.cache_clear() 重置。"""
    return Settings()
