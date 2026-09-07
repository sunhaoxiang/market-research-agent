"""分层并行执行研究任务（§7.1 步骤 4 / §7.2，P1-10）。

三条硬约束都在这里落地：

- **`max_parallel_tasks`**：限制的是同时在跑的任务数，为的是别把外部 API 的
  免费额度一次打爆（§8.3）。层与层之间本来就是串行的，所以信号量只在层内生效。
- **`task_timeout_s`**：单任务超时。实际取 `min(task_timeout_s, 整体剩余)`，
  否则最后一个任务能把报告撰写的时间全吃掉。
- **`max_session_cost_usd`**：每层开始前检查。放在层边界而不是任务边界，
  是因为同层任务已经并发出去了，中途叫停只会得到一堆半成品。

**失败降级是这一层的主要职责**（§7.2）：单个任务失败或超时都不中断流程，
任务标记为 failed，缺口进入报告的「数据限制」章节。整体失败只由 Planner 或
Report Writer 触发——它们没有降级形态，缺了就没有报告。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import structlog

from agent_service.observability.sdk_events import MAX_SUMMARY_CHARS
from agent_service.schemas.common import TaskStatus
from agent_service.schemas.events import (
    AgentCompletedEvent,
    AgentCompletedPayload,
    AgentFailedEvent,
    AgentFailedPayload,
    AgentStartedEvent,
    AgentStartedPayload,
    ErrorInfo,
    WarningEvent,
    WarningPayload,
)

if TYPE_CHECKING:
    from agent_service.config import ExecutionLimits
    from agent_service.orchestrator.state import ResearchState
    from agent_service.schemas.common import AgentName
    from agent_service.schemas.findings import ResearchFinding
    from agent_service.schemas.plan import ResearchTask

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class TaskContext:
    """交给子 Agent 执行的一个任务及其上游产出。"""

    task: ResearchTask
    upstream: tuple[ResearchFinding, ...] = ()
    """`depends_on` 指向的任务的结果。"""
    missing_upstream: tuple[str, ...] = ()
    """失败或被跳过的依赖 id。执行仍会继续（见模块说明），但结果里要披露。"""


class TaskRunner(Protocol):
    """执行单个任务的能力。真正的实现是 Phase 2-4 的子 Agent。

    拆成协议而不是直接写死，是因为「怎么跑一个任务」在 Phase 2 之前还不存在，
    而分层、超时、降级这些编排逻辑现在就能定稿并测试。
    """

    def model_id_for(self, agent: AgentName) -> str:
        """该 Agent 将使用的模型 id。仅用于填 `AGENT_STARTED` 事件。"""
        ...

    async def run(self, context: TaskContext, state: ResearchState) -> ResearchFinding:
        """执行任务。用量与成本由实现方通过 `state.record_run()` 登记。"""
        ...


async def execute_plan(
    state: ResearchState,
    *,
    runner: TaskRunner,
    limits: ExecutionLimits,
    deadline_s: float,
) -> None:
    """按依赖分层执行整个计划，结果写回 `state`。

    `deadline_s` 是留给执行阶段的秒数，由调用方从 `total_timeout_s` 里划出。
    这样 Phase 5 想为报告撰写预留时间时，改的是流程编排而不是执行器。
    """
    if state.plan is None:
        msg = "execute_plan 需要先 attach_plan"
        raise RuntimeError(msg)

    semaphore = asyncio.Semaphore(limits.max_parallel_tasks)

    for index, layer in enumerate(state.plan.layers, start=1):
        reason = _stop_reason(state, limits, deadline_s)
        if reason is not None:
            _skip_remaining(state, layer_index=index, reason=reason)
            break

        log.info("executor.layer_started", layer=index, tasks=[task.id for task in layer])
        await asyncio.gather(
            *(_guarded(task, state, runner=runner, limits=limits, gate=semaphore) for task in layer)
        )


# ─────────────────────────────────────────────────────────────────────────────
# 单任务
# ─────────────────────────────────────────────────────────────────────────────


async def _guarded(
    task: ResearchTask,
    state: ResearchState,
    *,
    runner: TaskRunner,
    limits: ExecutionLimits,
    gate: asyncio.Semaphore,
) -> None:
    """跑一个任务，把失败关在任务内部。

    刻意不用 `gather(return_exceptions=True)`：那样异常会汇总到调用方，
    而降级处理需要知道**是哪个任务**失败了才能标状态、发事件。
    """
    async with gate:
        context = _context_for(task, state)
        state.mark(task.id, TaskStatus.RUNNING)
        state.bus.emit(
            AgentStartedEvent,
            payload=AgentStartedPayload(
                agent=task.agent,
                task_id=task.id,
                objective=task.objective,
                model_id=runner.model_id_for(task.agent),
            ),
            message=task.objective[:MAX_SUMMARY_CHARS],
        )

        started_ms = state.elapsed_ms
        timeout_s = min(limits.task_timeout_s, state.remaining_s(limits.total_timeout_s))

        try:
            async with asyncio.timeout(timeout_s):
                finding = await runner.run(context, state)
        except TimeoutError:
            _fail(
                state,
                task,
                code="task_timeout",
                message=f"任务超过 {timeout_s:.0f}s 未完成",
            )
            return
        except asyncio.CancelledError:
            # 取消是外部意图（用户点了停止、整体超时），不是任务失败。
            # 吞掉它会让 pipeline 的取消传播断在这里，会话再也停不下来。
            state.mark(task.id, TaskStatus.SKIPPED)
            raise
        # 降级的边界就在这里：任务内的任何异常都不该冒泡到 gather
        except Exception as error:
            _fail(state, task, code=type(error).__name__, message=str(error))
            return

        if context.missing_upstream:
            finding = _disclose_missing_upstream(finding, context.missing_upstream)

        state.add_finding(finding)
        state.bus.emit(
            AgentCompletedEvent,
            payload=AgentCompletedPayload(
                agent=task.agent,
                task_id=task.id,
                summary=finding.summary,
                claim_count=len(finding.claims),
                source_count=len(finding.sources),
                duration_ms=state.elapsed_ms - started_ms,
            ),
        )


def _context_for(task: ResearchTask, state: ResearchState) -> TaskContext:
    """收集任务的上游产出。

    依赖失败时**照常执行**而不是跳过。理由：prompt 要求每个 objective 自包含，
    多数任务在缺少上游数据时仍能完成大部分工作（"对比 A 和 B" 里 A 挂了，
    B 的数据照样有价值）；跳过则会把一次失败放大成整条依赖链的失败，
    与 §7.2「不中断整个流程」相悖。代价是这类任务的产出会有缺口，
    所以必须显式披露——见 `_disclose_missing_upstream`。
    """
    by_id = {finding.task_id: finding for finding in state.findings}
    failed = state.failed_task_ids()
    return TaskContext(
        task=task,
        upstream=tuple(by_id[dep] for dep in task.depends_on if dep in by_id),
        missing_upstream=tuple(dep for dep in task.depends_on if dep in failed),
    )


def _disclose_missing_upstream(
    finding: ResearchFinding, missing: tuple[str, ...]
) -> ResearchFinding:
    """把缺失的上游输入记为 data_gap。

    这条缺口会进入报告的「数据限制」章节。不披露的后果比缺数据更糟：
    读者会以为这一节是在完整信息下得出的结论。
    """
    gap = f"依赖任务 {', '.join(missing)} 未成功，本任务缺少其产出作为输入"
    return finding.model_copy(update={"data_gaps": [*finding.data_gaps, gap]})


def _fail(state: ResearchState, task: ResearchTask, *, code: str, message: str) -> None:
    state.mark(task.id, TaskStatus.FAILED)
    log.warning("executor.task_failed", task_id=task.id, agent=task.agent.value, code=code)
    state.bus.emit(
        AgentFailedEvent,
        payload=AgentFailedPayload(
            agent=task.agent,
            task_id=task.id,
            error=ErrorInfo(code=code, message=message[:MAX_SUMMARY_CHARS]),
        ),
        message=f"任务失败：{task.objective[:80]}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 层边界的护栏
# ─────────────────────────────────────────────────────────────────────────────


def _stop_reason(
    state: ResearchState, limits: ExecutionLimits, deadline_s: float
) -> tuple[str, str] | None:
    """返回 (code, message)，None 表示可以继续。"""
    if state.over_budget(limits.max_session_cost_usd):
        return (
            "budget_exhausted",
            f"会话成本已达上限 ${limits.max_session_cost_usd:.2f}，剩余任务跳过",
        )
    remaining = deadline_s - state.elapsed_ms / 1000
    if remaining <= 0:
        return ("time_exhausted", f"研究阶段已用满 {deadline_s:.0f}s，剩余任务跳过")
    return None


def _skip_remaining(state: ResearchState, *, layer_index: int, reason: tuple[str, str]) -> None:
    """把尚未完成的任务全部标记为 skipped 并发一条 warning。

    只发一条聚合 warning 而不是每个任务一条：跳过的原因是同一个，
    逐条发只会把事件流刷满而不增加信息。
    """
    code, message = reason
    skipped = [
        task_id for task_id, status in state.task_status.items() if status is TaskStatus.PENDING
    ]
    for task_id in skipped:
        state.mark(task_id, TaskStatus.SKIPPED)

    log.warning("executor.skipped", layer=layer_index, code=code, tasks=skipped)
    state.bus.emit(
        WarningEvent,
        payload=WarningPayload(code=code, message=f"{message}（{', '.join(skipped)}）"),
        message=message,
    )
