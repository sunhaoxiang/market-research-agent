"""按任务类型分派子 Agent（P5-2 / [DP 决策 B]）。

web / crypto / stock 已装成可被编排层调用的专职 Agent，结果回到
`ResearchFinding`。这是 Agents-as-Tools 的落地：**调用方是 Python 执行器**，
不是 Research Manager——Manager 仍然没有工具（决策 A）。Handoff 会转移
控制权且不返回，用不了「多路并行 + 汇总」。

fact_checker 仍走占位（P5-5）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agent_service.agents.crypto_research import (
    CryptoResearchAgent,
    build_crypto_research,
    crypto_research_user_message,
)
from agent_service.agents.findings import (
    EMPTY_CRYPTO_SOURCES_GAP,
    EMPTY_STOCK_SOURCES_GAP,
    assemble_finding,
    format_upstream_finding,
    salvage_finding,
)
from agent_service.agents.placeholder import PlaceholderRunner
from agent_service.agents.runtime import run_tool_agent
from agent_service.agents.stock_research import (
    StockResearchAgent,
    build_stock_research,
    stock_research_user_message,
)
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
type _BuiltAgent = WebResearchAgent | CryptoResearchAgent | StockResearchAgent


@dataclass(frozen=True)
class _Assembled:
    """一个已装好的子 Agent：编排层按任务类型取用。"""

    built: _BuiltAgent
    user_message: _UserMessage
    empty_sources_gap: str | None = None


class SubAgentRunner:
    """`TaskRunner`：把三个研究 Agent 装进一次会话，fact_checker 仍是占位。"""

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
        web: WebResearchAgent | None = None,
        crypto: CryptoResearchAgent | None = None,
        stock: StockResearchAgent | None = None,
    ) -> None:
        assembled_web = web or build_web_research(registry)
        assembled_crypto = crypto or build_crypto_research(registry)
        assembled_stock = stock or build_stock_research(registry)
        self._agents: dict[AgentName, _Assembled] = {
            AgentName.WEB_RESEARCH: _Assembled(assembled_web, web_research_user_message),
            AgentName.CRYPTO_RESEARCH: _Assembled(
                assembled_crypto,
                crypto_research_user_message,
                EMPTY_CRYPTO_SOURCES_GAP,
            ),
            AgentName.STOCK_RESEARCH: _Assembled(
                assembled_stock,
                stock_research_user_message,
                EMPTY_STOCK_SOURCES_GAP,
            ),
        }
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
        assembled = self._agents.get(agent)
        if assembled is not None:
            return assembled.built.model_id
        return self._placeholder.model_id_for(agent)

    async def run(self, context: TaskContext, state: ResearchState) -> ResearchFinding:
        assembled = self._agents.get(context.task.agent)
        if assembled is None:
            return await self._placeholder.run(context, state)
        return await self._run_built(assembled, context, state)

    async def _run_built(
        self,
        assembled: _Assembled,
        context: TaskContext,
        state: ResearchState,
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
        user_input = assembled.user_message(
            context.task,
            now=now,
            upstream_summaries=tuple(format_upstream_finding(item) for item in context.upstream),
            missing_upstream=context.missing_upstream,
        )
        kwargs: dict[str, Any] = {}
        if assembled.empty_sources_gap is not None:
            kwargs["empty_sources_gap"] = assembled.empty_sources_gap

        try:
            structured = await run_tool_agent(
                assembled.built.agent,
                user_input,
                strategy=assembled.built.strategy,
                deps=deps,
                translator=translator,
                max_turns=self._limits.max_tool_calls_per_agent + 1,
            )
        except asyncio.CancelledError:
            # 执行器的 asyncio.timeout 会取消本协程。先把 collector 里已有的
            # 来源和 unsupported 缺口塞进 salvage，再让取消冒泡——否则 Writer
            # 只能写「数据缺失」，工具结果全废。
            self._record(
                assembled.built,
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
            self._record(
                assembled.built, state, started, context.task, usage=error.usage, error=error
            )
            raise
        except Exception as error:
            self._record(
                assembled.built,
                state,
                started,
                context.task,
                usage=TokenUsage(),
                error=ErrorInfo(code=type(error).__name__, message=str(error)[:200]),
            )
            raise

        self._record(assembled.built, state, started, context.task, usage=structured.usage)
        return assemble_finding(context.task, structured.output, collector, **kwargs)

    def _record(
        self,
        built: _BuiltAgent,
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
