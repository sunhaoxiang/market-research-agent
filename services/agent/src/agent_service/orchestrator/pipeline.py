"""研究会话全流程（§7.1，P1-10）。

当前覆盖 §7.1 的步骤 1-4（意图/规划/校验/执行）。步骤 5-9（合并去重、事实核查、
补充研究、报告撰写、输出护栏）在 Phase 5 接入，届时在 `researching` 之后追加
`checking` / `writing` 两个阶段——`Stage` 枚举与状态机已经为它们留好位置。

**这一层唯一的职责是「保证事件流一定终止」。** 无论正常完成、规划失败、
被取消还是撞上未预期的异常，都必须恰好发出一个终态事件（§12 / §14.1）：
前端与落库逻辑都依赖它来关连接、写终态。漏发一次，用户看到的就是一个
永远转圈的进度条。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from agent_service.orchestrator.executor import execute_plan
from agent_service.orchestrator.plan_validation import PlanRejectedError
from agent_service.orchestrator.planner import create_plan
from agent_service.orchestrator.state import ResearchState
from agent_service.schemas.common import Stage, TaskStatus
from agent_service.schemas.events import (
    ErrorInfo,
    SessionCancelledEvent,
    SessionCompletedEvent,
    SessionCompletedPayload,
    SessionFailedEvent,
    SessionFailedPayload,
    SessionStartedEvent,
    SessionStartedPayload,
)
from agent_service.utils.ids import new_id

if TYPE_CHECKING:
    from agent_service.agents.research_manager import PlannerAgent
    from agent_service.config import ExecutionLimits
    from agent_service.observability.event_bus import EventBus
    from agent_service.orchestrator.executor import TaskRunner
    from agent_service.schemas.findings import ResearchFinding

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ResearchOutcome:
    """一次会话的最终结果。

    `findings` 现在直接返回给调用方；Phase 5 起它会先流向事实核查与
    Report Writer，本对象再补上 `report` 字段。
    """

    session_id: str
    state: ResearchState
    succeeded: bool

    @property
    def findings(self) -> list[ResearchFinding]:
        return self.state.findings


async def run_research(
    question: str,
    *,
    planner: PlannerAgent,
    runner: TaskRunner,
    limits: ExecutionLimits,
    bus: EventBus,
    session_id: str | None = None,
    now: datetime | None = None,
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
        await _plan_and_execute(state, planner=planner, runner=runner, limits=limits, now=now)
    except PlanRejectedError as error:
        # 规划失败没有降级形态：没有计划就没有任务可执行（§7.2）
        return _fail(state, code="plan_rejected", message=error.reason)
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
    limits: ExecutionLimits,
    now: datetime | None,
) -> None:
    state.advance_to(Stage.PLANNING)
    planning = await create_plan(
        state.question,
        planner=planner,
        limits=limits,
        bus=state.bus,
        now=now,
    )
    state.attach_plan(planning.validated)
    state.record_run(
        planner.entry,
        planning.run_result.context_wrapper.usage,
        at=now or datetime.now(UTC),
    )

    state.advance_to(Stage.RESEARCHING)
    await execute_plan(
        state,
        runner=runner,
        limits=limits,
        # 规划已经花掉的时间要从预算里扣掉，否则总耗时会超出 total_timeout_s
        deadline_s=state.remaining_s(limits.total_timeout_s),
    )


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
