"""按任务类型分派子 Agent。web_research 已接真工具；其余仍走占位。"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agent_service.agents.placeholder import PlaceholderRunner
from agent_service.agents.runtime import run_tool_agent
from agent_service.agents.web_research import (
    assemble_finding,
    build_web_research,
    web_research_user_message,
)
from agent_service.models.structured_output import StructuredOutputError
from agent_service.observability.sdk_events import AgentRunTranslator
from agent_service.orchestrator.state import AgentRun
from agent_service.schemas.common import AgentName
from agent_service.schemas.events import ErrorInfo, TokenUsage
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


class SubAgentRunner:
    """`TaskRunner`：web_research 走真 Agent，其它任务仍是占位。"""

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        limits: ExecutionLimits,
        search: SearchProvider | None = None,
        fetcher: PageFetcher | None = None,
        clock: Clock | None = None,
        fallback_model_id: str,
    ) -> None:
        self._web = build_web_research(registry)
        self._placeholder = PlaceholderRunner(fallback_model_id)
        self._limits = limits
        self._search = search
        self._fetcher = fetcher
        self._clock = clock

    def model_id_for(self, agent: AgentName) -> str:
        if agent is AgentName.WEB_RESEARCH:
            return self._web.model_id
        return self._placeholder.model_id_for(agent)

    async def run(self, context: TaskContext, state: ResearchState) -> ResearchFinding:
        if context.task.agent is AgentName.WEB_RESEARCH:
            return await self._run_web(context, state)
        return await self._placeholder.run(context, state)

    async def _run_web(self, context: TaskContext, state: ResearchState) -> ResearchFinding:
        started = time.monotonic()
        collector = SourceCollector()
        deps = ToolDeps(
            search=self._search,
            fetcher=self._fetcher,
            clock=self._clock,
            bus=state.bus,
            sources=collector,
        )
        translator = AgentRunTranslator(
            state.bus, agent=AgentName.WEB_RESEARCH, task_id=context.task.id
        )
        now = self._clock.now() if self._clock is not None else datetime.now(UTC)
        user_input = web_research_user_message(
            context.task,
            now=now,
            upstream_summaries=tuple(item.summary for item in context.upstream),
            missing_upstream=context.missing_upstream,
        )

        try:
            structured = await run_tool_agent(
                self._web.agent,
                user_input,
                strategy=self._web.strategy,
                deps=deps,
                translator=translator,
                max_turns=self._limits.max_tool_calls_per_agent + 1,
            )
        except StructuredOutputError as error:
            self._record(state, started, context.task.id, usage=error.usage, error=error)
            raise
        except Exception as error:
            self._record(
                state,
                started,
                context.task.id,
                usage=TokenUsage(),
                error=ErrorInfo(code=type(error).__name__, message=str(error)[:200]),
            )
            raise

        self._record(state, started, context.task.id, usage=structured.usage)
        return assemble_finding(context.task, structured.output, collector)

    def _record(
        self,
        state: ResearchState,
        started: float,
        task_id: str,
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
                agent=AgentName.WEB_RESEARCH,
                model=self._web.entry,
                duration_ms=int((time.monotonic() - started) * 1000),
                token_usage_override=usage,
                task_id=task_id,
                prompt=self._web.prompt,
                error=info,
            )
        )
