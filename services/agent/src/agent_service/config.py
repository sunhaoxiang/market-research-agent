"""集中式配置。所有环境变量只在此处读取，业务代码通过 get_settings() 获取。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根目录：src/agent_service/config.py → 上溯 4 层
REPO_ROOT = Path(__file__).resolve().parents[4]


class ProviderCredentials(BaseSettings):
    """LLM provider 的 key 与 base_url。缺失的 provider 在 ModelRegistry 层被标记为不可用。"""

    model_config = SettingsConfigDict(extra="ignore")

    # OpenAI 为首选 provider；该 key 同时用于 SDK tracing 上传（见 §9.6）
    openai_api_key: SecretStr | None = None
    moonshot_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    zhipu_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None

    openai_base_url: str | None = None
    moonshot_base_url: str = "https://api.moonshot.cn/v1"
    deepseek_base_url: str = "https://api.deepseek.com"
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"


class ExecutionLimits(BaseSettings):
    """研究流程的硬上限，防止成本与延迟失控（DEVELOPMENT_PLAN.md §7.2）。"""

    model_config = SettingsConfigDict(extra="ignore")

    max_tasks_per_plan: int = Field(default=6, ge=1, le=10)
    max_parallel_tasks: int = Field(default=4, ge=1, le=6)
    max_tool_calls_per_agent: int = Field(default=12, ge=1, le=20)
    max_supplement_rounds: int = Field(default=1, ge=0, le=2)
    task_timeout_s: float = Field(default=120.0, gt=0)
    total_timeout_s: float = Field(default=420.0, gt=0)
    max_session_cost_usd: float = Field(default=1.0, gt=0)


class Settings(BaseSettings):
    """服务全局配置。"""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env.local", REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── 服务
    agent_service_host: str = "127.0.0.1"
    agent_service_port: int = 8000
    internal_api_token: SecretStr | None = None
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # ── 数据源
    tavily_api_key: SecretStr | None = None
    coingecko_api_key: SecretStr | None = None
    fmp_api_key: SecretStr | None = None
    sec_user_agent: str = "market-research-agent contact@example.com"

    # ── 缓存
    agent_cache_db: Path = REPO_ROOT / "data" / "provider-cache.db"

    # ── 模型（见 §9.5 / §9.7）。开发期用国内模型，上线切 OpenAI 只改环境变量
    default_model_id: str = "deepseek:deepseek-v4-pro"
    model_role_planner: str | None = None
    model_role_balanced: str | None = None
    model_role_fast: str | None = None
    model_role_writing: str | None = None

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
