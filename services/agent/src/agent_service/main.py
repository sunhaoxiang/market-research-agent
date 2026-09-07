"""FastAPI 应用入口。

Phase 0 只提供健康检查；Agent / Tool / 研究流程在 Phase 1 起逐步接入。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import orjson
from fastapi import FastAPI, Response
from pydantic import BaseModel

from agent_service import __version__
from agent_service.config import Settings, get_settings
from agent_service.observability.logging import configure_logging, get_logger

log = get_logger(__name__)


class ORJSONResponse(Response):
    """orjson 序列化，比默认 json 快且原生支持 datetime。"""

    media_type = "application/json"

    def render(self, content: object) -> bytes:
        return orjson.dumps(content)


class ProviderStatus(BaseModel):
    """某个 LLM provider 是否已配置 key。绝不返回 key 本身（§17.1）。"""

    provider: str
    configured: bool


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    tracing_enabled: bool
    llm_providers: list[ProviderStatus]
    data_sources: list[ProviderStatus]


def _llm_provider_status(settings: Settings) -> list[ProviderStatus]:
    creds = settings.providers
    return [
        ProviderStatus(provider="openai", configured=creds.openai_api_key is not None),
        ProviderStatus(provider="moonshot", configured=creds.moonshot_api_key is not None),
        ProviderStatus(provider="deepseek", configured=creds.deepseek_api_key is not None),
        ProviderStatus(provider="zhipu", configured=creds.zhipu_api_key is not None),
        ProviderStatus(provider="anthropic", configured=creds.anthropic_api_key is not None),
        ProviderStatus(provider="google", configured=creds.google_api_key is not None),
    ]


def _data_source_status(settings: Settings) -> list[ProviderStatus]:
    return [
        ProviderStatus(provider="tavily", configured=settings.tavily_api_key is not None),
        ProviderStatus(provider="coingecko", configured=settings.coingecko_api_key is not None),
        ProviderStatus(provider="fmp", configured=settings.fmp_api_key is not None),
        # 这两个不需要 key
        ProviderStatus(provider="defillama", configured=True),
        ProviderStatus(provider="sec_edgar", configured=True),
    ]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    settings.cache_db_path.parent.mkdir(parents=True, exist_ok=True)

    configured = [p.provider for p in _llm_provider_status(settings) if p.configured]
    log.info(
        "agent_service.startup",
        version=__version__,
        configured_llm_providers=configured,
        default_model_id=settings.default_model_id,
        tracing_enabled=not settings.openai_agents_disable_tracing,
    )
    if not configured:
        log.warning(
            "agent_service.no_llm_provider",
            hint="未配置任何 LLM provider key，研究流程将无法运行。见 .env.example",
        )

    yield

    log.info("agent_service.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Market Research Agent Service",
        description="AI Financial Research Agent for Crypto & US Stocks",
        version=__version__,
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )

    @app.get("/v1/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        settings = get_settings()
        llm = _llm_provider_status(settings)
        return HealthResponse(
            status="ok" if any(p.configured for p in llm) else "degraded",
            version=__version__,
            tracing_enabled=not settings.openai_agents_disable_tracing,
            llm_providers=llm,
            data_sources=_data_source_status(settings),
        )

    return app


app = create_app()
