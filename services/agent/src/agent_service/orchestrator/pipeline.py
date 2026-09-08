"""研究会话全流程（§7.1，P1-10 / P2-8 / P5-4）。

当前覆盖 §7.1 的步骤 1-5 与步骤 8（意图/规划/校验/执行/Merge & Dedup/撰写）。
步骤 5 在执行之后跑：按 url_canonical 合并跨 Agent 来源、折叠重复陈述、
汇总数值冲突。会话内 SourceRegistry 仍在 tool 登记时去重（P2-7）；Merge
负责 findings 里漏进来的重复副本。
步骤 6-7、9（事实核查、补充研究、输出护栏）在后续阶段接入。
`Stage` 枚举已经为 `checking` 留好位置。

**这一层唯一的职责是「保证事件流一定终止」。** 无论正常完成、规划失败、
被取消还是撞上未预期的异常，都必须恰好发出一个终态事件（§12 / §14.1）：
前端与落库逻辑都依赖它来关连接、写终态。漏发一次，用户看到的就是一个
永远转圈的进度条。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from agent_service.models.structured_output import StructuredOutputError
from agent_service.orchestrator.executor import execute_plan
from agent_service.orchestrator.intent import Intent, IntentClassifier, classify_intent
from agent_service.orchestrator.merge import merge_research
from agent_service.orchestrator.plan_validation import PlanRejectedError
from agent_service.orchestrator.planner import PlanningResult, create_plan
from agent_service.orchestrator.state import AgentRun, ResearchState
from agent_service.orchestrator.writer import write_report
from agent_service.schemas.common import AgentName, Stage, TaskStatus
from agent_service.schemas.events import (
    ErrorInfo,
    SessionCancelledEvent,
    SessionCompletedEvent,
    SessionCompletedPayload,
    SessionFailedEvent,
    SessionFailedPayload,
    SessionStartedEvent,
    SessionStartedPayload,
    TokenUsage,
)
from agent_service.utils.ids import new_id

if TYPE_CHECKING:
    from agent_service.agents.report_writer import ReportWriterAgent
    from agent_service.agents.research_manager import PlannerAgent
    from agent_service.config import ExecutionLimits
    from agent_service.observability.event_bus import EventBus
    from agent_service.orchestrator.executor import TaskRunner
    from agent_service.schemas.findings import ResearchFinding
    from agent_service.schemas.report import ResearchReport

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ResearchOutcome:
    """一次会话的最终结果。

    `findings` 与 `report` 一并返回。Fact Checker 接入后，findings 会先
    流向核查，再交给 Report Writer。
    """

    session_id: str
    state: ResearchState
    succeeded: bool

    @property
    def findings(self) -> list[ResearchFinding]:
        return self.state.findings

    @property
    def report(self) -> ResearchReport | None:
        return self.state.report


async def run_research(
    question: str,
    *,
    planner: PlannerAgent,
    runner: TaskRunner,
    writer: ReportWriterAgent,
    limits: ExecutionLimits,
    bus: EventBus,
    session_id: str | None = None,
    now: datetime | None = None,
    classifier: IntentClassifier | None = None,
) -> ResearchOutcome:
    """跑完一次研究。

    不抛业务异常：失败通过 `SESSION_FAILED` 事件与 `outcome.succeeded`
    表达。SSE 端点在 `await` 这个协程的同时把 `bus.stream()` 推给客户端，
    协程若抛异常，端点就得在两个地方处理失败——而事件流里已经有 session_failed
    了，两套失败通路迟早不一致。

    `asyncio.CancelledError` 是唯一的例外：它必须继续向上传播，否则
    `asyncio.timeout` 与 `task.cancel()` 都会失效（见 §7.2 的取消语义）。
    """
    session_id = session_id or new_id()
    state = ResearchState(session_id, question, bus=bus)

    bus.emit(
        SessionStartedEvent,
        payload=SessionStartedPayload(question=question, model_id=planner.model_id),
        message="已收到问题，开始研究",
    )

    try:
        await _plan_and_execute(
            state,
            planner=planner,
            runner=runner,
            writer=writer,
            limits=limits,
            now=now,
            classifier=classifier,
        )
    except PlanRejectedError as error:
        # 规划失败没有降级形态：没有计划就没有任务可执行（§7.2）
        return _fail(state, code="plan_rejected", message=error.reason)
    except StructuredOutputError as error:
        # Planner 与 Report Writer 都没有降级形态（§7.2）。用 stage 区分是哪一步。
        return _fail(state, code="structured_output", message=str(error))
    except asyncio.CancelledError:
        bus.emit(
            SessionCancelledEvent,
            payload=None,
            message="研究已取消",
        )
        raise
    # 兜底：无论什么异常都要先把终态事件发出去，否则前端永远转圈
    except Exception as error:
        log.exception("pipeline.unexpected_failure", session_id=session_id)
        return _fail(state, code=type(error).__name__, message=str(error))

    bus.emit(
        SessionCompletedEvent,
        payload=SessionCompletedPayload(
            duration_ms=state.elapsed_ms,
            usage=state.usage,
            cost_usd=state.cost_usd,
        ),
        message=_completion_message(state),
    )
    return ResearchOutcome(session_id=session_id, state=state, succeeded=True)


async def _plan_and_execute(
    state: ResearchState,
    *,
    planner: PlannerAgent,
    runner: TaskRunner,
    writer: ReportWriterAgent,
    limits: ExecutionLimits,
    now: datetime | None,
    classifier: IntentClassifier | None,
) -> None:
    state.advance_to(Stage.PLANNING)
    intent = await classify_intent(state.question, classifier=classifier, bus=state.bus)
    planning = await _plan(state, planner=planner, limits=limits, now=now, intent=intent)
    state.attach_plan(planning.validated)

    state.advance_to(Stage.RESEARCHING)
    await execute_plan(
        state,
        runner=runner,
        limits=limits,
        # 规划已经花掉的时间要从预算里扣掉，否则总耗时会超出 total_timeout_s
        deadline_s=state.remaining_s(limits.total_timeout_s),
    )
    merge_research(state)

    state.advance_to(Stage.WRITING)
    await write_report(state, writer, now=now)


async def _plan(
    state: ResearchState,
    *,
    planner: PlannerAgent,
    limits: ExecutionLimits,
    now: datetime | None,
    intent: Intent | None,
) -> PlanningResult:
    """跑规划，并且**无论成败**都把这次 run 记进埋点。

    失败路径同样要记账：规划调用失败时钱已经花了，而"规划反复重试后失败"
    恰恰是最贵的一种会话。只在成功时记录会让 `/debug` 里的成本系统性偏低，
    且偏低的幅度正好集中在最该被关注的那些会话上（§20.1）。
    """
    started = time.monotonic()

    def record(usage: TokenUsage, error: ErrorInfo | None = None) -> None:
        state.record_run(
            AgentRun(
                agent=AgentName.RESEARCH_MANAGER,
                model=planner.entry,
                duration_ms=int((time.monotonic() - started) * 1000),
                token_usage_override=usage,
                prompt=planner.prompt,
                error=error,
            ),
            at=now or datetime.now(UTC),
        )

    try:
        planning = await create_plan(
            state.question,
            planner=planner,
            limits=limits,
            bus=state.bus,
            now=now,
            intent=intent,
        )
    except PlanRejectedError as error:
        record(error.usage, ErrorInfo(code="plan_rejected", message=error.reason))
        raise
    except StructuredOutputError as error:
        record(error.usage, ErrorInfo(code="structured_output", message=str(error)))
        raise

    record(planning.usage)
    return planning


def _fail(state: ResearchState, *, code: str, message: str) -> ResearchOutcome:
    state.bus.emit(
        SessionFailedEvent,
        payload=SessionFailedPayload(
            error=ErrorInfo(code=code, message=message),
            stage=state.stage,
        ),
        message="研究未能完成",
    )
    return ResearchOutcome(session_id=state.session_id, state=state, succeeded=False)


def _completion_message(state: ResearchState) -> str:
    """完成时给用户的一句话。

    只报「几个任务成功」不够——用户看到 3 个任务的计划却只拿到 2 份结果时，
    需要知道那不是 bug。所以失败/跳过的数量也要说出来。
    """
    total = len(state.task_status)
    done = sum(1 for status in state.task_status.values() if status is TaskStatus.COMPLETED)
    if done == total:
        return f"研究完成，{total} 个任务全部成功"
    return f"研究完成，{done}/{total} 个任务成功（其余失败或跳过，详见数据限制）"
