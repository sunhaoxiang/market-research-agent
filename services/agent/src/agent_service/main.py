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
from agent_service.api import models as models_api
from agent_service.api import research as research_api
from agent_service.api import tools as tools_api
from agent_service.config import Settings, get_settings
from agent_service.models.registry import ModelRegistry, bootstrap_sdk, tracing_status
from agent_service.observability.logging import configure_logging, get_logger
from agent_service.providers.crypto import CoinGeckoProvider
from agent_service.providers.defi import DefiLlamaProvider
from agent_service.providers.fetch import WebFetcher
from agent_service.providers.onchain import HyperliquidProvider
from agent_service.providers.runtime import ProviderRuntime
from agent_service.providers.search import TavilySearchProvider

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
        # Demo 档无 key 也能打公共限流，所以始终可用；有 key 只是额度更高
        ProviderStatus(provider="coingecko", configured=True),
        ProviderStatus(provider="fmp", configured=settings.fmp_api_key is not None),
        # 这些不需要 key
        ProviderStatus(provider="defillama", configured=True),
        ProviderStatus(provider="hyperliquid", configured=True),
        ProviderStatus(provider="sec_edgar", configured=True),
    ]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    settings.cache_db_path.parent.mkdir(parents=True, exist_ok=True)

    tracing_enabled = bootstrap_sdk(settings)
    app.state.registry = ModelRegistry(settings)

    runtime = ProviderRuntime(settings.cache_db_path)
    await runtime.open()
    app.state.provider_runtime = runtime

    search_provider = None
    tavily_key = settings.tavily_api_key
    if tavily_key is not None:
        search_provider = TavilySearchProvider(
            runtime=runtime, api_key=tavily_key.get_secret_value()
        )
    app.state.search_provider = search_provider
    app.state.web_fetcher = WebFetcher(runtime)
    # Demo 档无 key 也能打公共限流；tool 层（P3-3）注入 ToolDeps
    cg_key = settings.coingecko_api_key
    app.state.coingecko = CoinGeckoProvider(
        runtime=runtime,
        api_key=None if cg_key is None else cg_key.get_secret_value(),
    )
    app.state.defillama = DefiLlamaProvider(runtime=runtime)
    app.state.hyperliquid = HyperliquidProvider(runtime=runtime)

    configured = [p.provider for p in _llm_provider_status(settings) if p.configured]
    log.info(
        "agent_service.startup",
        version=__version__,
        configured_llm_providers=configured,
        default_model_id=settings.default_model_id,
        tracing_enabled=tracing_enabled,
    )
    if not configured:
        log.warning(
            "agent_service.no_llm_provider",
            hint="未配置任何 LLM provider key，研究流程将无法运行。见 .env.example",
        )

    yield

    search = getattr(app.state, "search_provider", None)
    if search is not None:
        await search.aclose()
    fetcher = getattr(app.state, "web_fetcher", None)
    if fetcher is not None:
        await fetcher.aclose()
    coingecko = getattr(app.state, "coingecko", None)
    if coingecko is not None:
        await coingecko.aclose()
    defillama = getattr(app.state, "defillama", None)
    if defillama is not None:
        await defillama.aclose()
    hyperliquid = getattr(app.state, "hyperliquid", None)
    if hyperliquid is not None:
        await hyperliquid.aclose()
    await app.state.provider_runtime.aclose()
    await app.state.registry.aclose()
    log.info("agent_service.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Market Research Agent Service",
        description="AI Financial Research Agent for Crypto & US Stocks",
        version=__version__,
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )

    # registry 在 lifespan 中重建；这里先放一个，让不走 lifespan 的
    # TestClient(app) 与 `--reload` 首次导入也能正常响应
    app.state.registry = ModelRegistry(get_settings())
    app.state.search_provider = None
    app.state.web_fetcher = None
    app.state.coingecko = None
    app.state.defillama = None
    app.state.hyperliquid = None

    @app.get("/v1/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        settings = get_settings()
        llm = _llm_provider_status(settings)
        return HealthResponse(
            status="ok" if any(p.configured for p in llm) else "degraded",
            version=__version__,
            # 走 tracing_status() 而非直接读配置：没有 OpenAI key 时 tracing
            # 实际上是关闭的，健康检查不能宣称它开着
            tracing_enabled=tracing_status(settings).enabled,
            llm_providers=llm,
            data_sources=_data_source_status(settings),
        )

    app.include_router(models_api.router)
    app.include_router(research_api.router)
    app.include_router(tools_api.router)
    return app


app = create_app()
