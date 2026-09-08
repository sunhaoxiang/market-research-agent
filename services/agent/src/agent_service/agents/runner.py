"""按任务类型分派子 Agent。web / crypto 已接真工具；其余仍走占位。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agent_service.agents.crypto_research import (
    CryptoResearchAgent,
    build_crypto_research,
    crypto_research_user_message,
)
from agent_service.agents.findings import (
    EMPTY_CRYPTO_SOURCES_GAP,
    assemble_finding,
    salvage_finding,
)
from agent_service.agents.placeholder import PlaceholderRunner
from agent_service.agents.runtime import run_tool_agent
from agent_service.agents.web_research import (
    WebResearchAgent,
    build_web_research,
    web_research_user_message,
)
from agent_service.models.structured_output import StructuredOutputError
from agent_service.observability.sdk_events import AgentRunTranslator
from agent_service.orchestrator.state import AgentRun
from agent_service.schemas.common import AgentName
from agent_service.schemas.events import ErrorInfo, TokenUsage
from agent_service.schemas.plan import ResearchTask
from agent_service.tools.deps import ToolDeps
from agent_service.tools.web.collector import SourceCollector

if TYPE_CHECKING:
    from agent_service.config import ExecutionLimits
    from agent_service.models.registry import ModelRegistry
    from agent_service.orchestrator.executor import TaskContext
    from agent_service.orchestrator.state import ResearchState
    from agent_service.providers.fetch import PageFetcher
    from agent_service.providers.runtime import Clock
    from agent_service.providers.search import SearchProvider
    from agent_service.schemas.findings import ResearchFinding
    from agent_service.tools.deps import (
        CoinGeckoClient,
        DefiLlamaClient,
        FmpClient,
        HyperliquidClient,
        SecEdgarClient,
    )

type _UserMessage = Callable[..., str]


class SubAgentRunner:
    """`TaskRunner`：web_research / crypto_research 走真 Agent，其它任务仍是占位。"""

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        limits: ExecutionLimits,
        search: SearchProvider | None = None,
        fetcher: PageFetcher | None = None,
        coingecko: CoinGeckoClient | None = None,
        defillama: DefiLlamaClient | None = None,
        hyperliquid: HyperliquidClient | None = None,
        sec_edgar: SecEdgarClient | None = None,
        fmp: FmpClient | None = None,
        clock: Clock | None = None,
        fallback_model_id: str,
    ) -> None:
        self._web = build_web_research(registry)
        self._crypto = build_crypto_research(registry)
        self._placeholder = PlaceholderRunner(fallback_model_id)
        self._limits = limits
        self._search = search
        self._fetcher = fetcher
        self._coingecko = coingecko
        self._defillama = defillama
        self._hyperliquid = hyperliquid
        self._sec_edgar = sec_edgar
        self._fmp = fmp
        self._clock = clock

    def model_id_for(self, agent: AgentName) -> str:
        if agent is AgentName.WEB_RESEARCH:
            return self._web.model_id
        if agent is AgentName.CRYPTO_RESEARCH:
            return self._crypto.model_id
        return self._placeholder.model_id_for(agent)

    async def run(self, context: TaskContext, state: ResearchState) -> ResearchFinding:
        if context.task.agent is AgentName.WEB_RESEARCH:
            return await self._run_built(
                self._web,
                context,
                state,
                user_message=web_research_user_message,
            )
        if context.task.agent is AgentName.CRYPTO_RESEARCH:
            return await self._run_built(
                self._crypto,
                context,
                state,
                user_message=crypto_research_user_message,
                empty_sources_gap=EMPTY_CRYPTO_SOURCES_GAP,
            )
        return await self._placeholder.run(context, state)

    async def _run_built(
        self,
        built: WebResearchAgent | CryptoResearchAgent,
        context: TaskContext,
        state: ResearchState,
        *,
        user_message: _UserMessage,
        empty_sources_gap: str | None = None,
    ) -> ResearchFinding:
        started = time.monotonic()
        collector = SourceCollector(registry=state.source_registry)
        deps = ToolDeps(
            search=self._search,
            fetcher=self._fetcher,
            coingecko=self._coingecko,
            defillama=self._defillama,
            hyperliquid=self._hyperliquid,
            sec_edgar=self._sec_edgar,
            fmp=self._fmp,
            clock=self._clock,
            bus=state.bus,
            sources=collector,
        )
        translator = AgentRunTranslator(
            state.bus, agent=context.task.agent, task_id=context.task.id
        )
        now = self._clock.now() if self._clock is not None else datetime.now(UTC)
        user_input = user_message(
            context.task,
            now=now,
            upstream_summaries=tuple(item.summary for item in context.upstream),
            missing_upstream=context.missing_upstream,
        )

        kwargs: dict[str, Any] = {}
        if empty_sources_gap is not None:
            kwargs["empty_sources_gap"] = empty_sources_gap

        try:
            structured = await run_tool_agent(
                built.agent,
                user_input,
                strategy=built.strategy,
                deps=deps,
                translator=translator,
                max_turns=self._limits.max_tool_calls_per_agent + 1,
            )
        except asyncio.CancelledError:
            # 执行器的 asyncio.timeout 会取消本协程。先把 collector 里已有的
            # 来源和 unsupported 缺口塞进 salvage，再让取消冒泡——否则 Writer
            # 只能写「数据缺失」，工具结果全废。
            self._record(
                built,
                state,
                started,
                context.task,
                usage=TokenUsage(),
                error=ErrorInfo(
                    code="task_timeout",
                    message=f"任务超过 {context.timeout_s:.0f}s 未完成",
                ),
            )
            context.salvage.finding = salvage_finding(
                context.task,
                collector,
                timeout_s=context.timeout_s,
                **kwargs,
            )
            raise
        except StructuredOutputError as error:
            self._record(built, state, started, context.task, usage=error.usage, error=error)
            raise
        except Exception as error:
            self._record(
                built,
                state,
                started,
                context.task,
                usage=TokenUsage(),
                error=ErrorInfo(code=type(error).__name__, message=str(error)[:200]),
            )
            raise

        self._record(built, state, started, context.task, usage=structured.usage)
        return assemble_finding(context.task, structured.output, collector, **kwargs)

    def _record(
        self,
        built: WebResearchAgent | CryptoResearchAgent,
        state: ResearchState,
        started: float,
        task: ResearchTask,
        *,
        usage: TokenUsage,
        error: ErrorInfo | StructuredOutputError | None = None,
    ) -> None:
        info: ErrorInfo | None
        if isinstance(error, StructuredOutputError):
            info = ErrorInfo(code="structured_output", message=str(error)[:200])
        else:
            info = error
        state.record_run(
            AgentRun(
                agent=task.agent,
                model=built.entry,
                duration_ms=int((time.monotonic() - started) * 1000),
                token_usage_override=usage,
                task_id=task.id,
                prompt=built.prompt,
                error=info,
            )
        )
