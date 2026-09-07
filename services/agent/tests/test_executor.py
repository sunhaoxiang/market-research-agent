"""分层执行与失败降级（P1-10）。

执行器最要紧的性质不是「能跑完」，而是**坏路径下的行为**：
一个任务超时/抛异常/预算耗尽时，其余任务与整个会话必须照常推进（§7.2）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from agent_service.observability.event_bus import EventBus
from agent_service.orchestrator.executor import TaskContext, execute_plan
from agent_service.orchestrator.plan_validation import validate_plan
from agent_service.orchestrator.state import ResearchState
from agent_service.schemas.common import AgentName, QuestionType, TaskStatus
from agent_service.schemas.events import (
    AgentFailedPayload,
    EventType,
    ResearchEvent,
    WarningPayload,
)
from agent_service.schemas.findings import ResearchFinding
from agent_service.schemas.plan import ResearchPlan, ResearchTask
from agent_service.testing import IsolatedExecutionLimits

_MODEL_ID = "deepseek:deepseek-v4-pro"


# ─────────────────────────────────────────────────────────────────────────────
# 测试替身
# ─────────────────────────────────────────────────────────────────────────────


class FakeClock:
    """手动推进的单调时钟，让预算测试不必真的等。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@dataclass
class RecordingRunner:
    """记录调用顺序的 TaskRunner。

    `delays` / `failures` 让单个任务可以变慢或变坏，用来驱动降级路径；
    `burns` 则推进假时钟，用来在不真等的前提下把时间预算烧完。
    """

    delays: dict[str, float] = field(default_factory=dict)
    failures: dict[str, BaseException] = field(default_factory=dict)
    """用 BaseException 而非 Exception：`CancelledError` 不是 Exception 的子类，
    而它恰恰是这里最需要覆盖的一种「失败」（取消必须穿透而非被降级）。"""
    burns: float = 0.0
    clock: FakeClock | None = None
    started: list[str] = field(default_factory=list)
    finished: list[str] = field(default_factory=list)
    contexts: dict[str, TaskContext] = field(default_factory=dict)
    peak_concurrency: int = 0
    _in_flight: int = 0

    def model_id_for(self, agent: AgentName) -> str:
        del agent
        return _MODEL_ID

    async def run(self, context: TaskContext, state: ResearchState) -> ResearchFinding:
        del state
        task_id = context.task.id
        self.started.append(task_id)
        self.contexts[task_id] = context

        self._in_flight += 1
        self.peak_concurrency = max(self.peak_concurrency, self._in_flight)
        try:
            delay = self.delays.get(task_id, 0.0)
            if delay:
                await asyncio.sleep(delay)
            if task_id in self.failures:
                raise self.failures[task_id]
        finally:
            self._in_flight -= 1
            if self.clock is not None:
                self.clock.now += self.burns

        self.finished.append(task_id)
        return ResearchFinding(
            task_id=task_id,
            agent=context.task.agent,
            summary=f"{task_id} 的结论",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 夹具
# ─────────────────────────────────────────────────────────────────────────────


def _task(task_id: str, *, depends_on: list[str] | None = None) -> ResearchTask:
    return ResearchTask(
        id=task_id,
        agent=AgentName.CRYPTO_RESEARCH,
        objective=f"任务 {task_id}",
        depends_on=depends_on or [],
    )


def _limits(**overrides: float | int) -> IsolatedExecutionLimits:
    return IsolatedExecutionLimits(**overrides)  # pyright: ignore[reportArgumentType]


def _state(*tasks: ResearchTask, clock: FakeClock | None = None) -> ResearchState:
    bus = EventBus("sess-1", heartbeat_interval_s=60.0)
    state = ResearchState(
        "sess-1",
        "测试问题",
        bus=bus,
        clock=clock or (lambda: 0.0),
    )
    plan = ResearchPlan(
        question_type=QuestionType.CRYPTO,
        interpretation="测试",
        tasks=list(tasks),
    )
    state.attach_plan(validate_plan(plan, _limits()))
    return state


async def _drain(bus: EventBus) -> list[ResearchEvent]:
    bus.close()
    return [event async for event in bus.stream()]


def _payload[T](event: ResearchEvent, payload_type: type[T]) -> T:
    payload = event.payload
    assert isinstance(payload, payload_type)
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# 正常路径
# ─────────────────────────────────────────────────────────────────────────────


async def test_independent_tasks_run_concurrently() -> None:
    """同层任务必须真并行——串行的话计划里的 fan-out 设计就白做了。"""
    state = _state(_task("t1"), _task("t2"), _task("t3"))
    runner = RecordingRunner(delays={"t1": 0.02, "t2": 0.02, "t3": 0.02})

    await execute_plan(state, runner=runner, limits=_limits(), deadline_s=60.0)

    assert runner.peak_concurrency == 3
    assert len(state.findings) == 3


async def test_layers_run_in_order() -> None:
    state = _state(_task("t1"), _task("t2", depends_on=["t1"]))
    runner = RecordingRunner()

    await execute_plan(state, runner=runner, limits=_limits(), deadline_s=60.0)

    assert runner.started == ["t1", "t2"]


async def test_max_parallel_tasks_is_enforced() -> None:
    """外部数据源多是免费额度，同时打太多请求会直接触发限流（§8.3）。"""
    state = _state(*(_task(f"t{index}") for index in range(1, 7)))
    runner = RecordingRunner(delays=dict.fromkeys([f"t{i}" for i in range(1, 7)], 0.02))

    await execute_plan(state, runner=runner, limits=_limits(max_parallel_tasks=2), deadline_s=60.0)

    assert runner.peak_concurrency == 2
    assert len(state.findings) == 6


async def test_upstream_findings_reach_the_dependent_task() -> None:
    state = _state(_task("t1"), _task("t2", depends_on=["t1"]))
    runner = RecordingRunner()

    await execute_plan(state, runner=runner, limits=_limits(), deadline_s=60.0)

    upstream = runner.contexts["t2"].upstream
    assert [finding.task_id for finding in upstream] == ["t1"]


# ─────────────────────────────────────────────────────────────────────────────
# 失败降级（§7.2）
# ─────────────────────────────────────────────────────────────────────────────


async def test_one_failure_does_not_stop_the_others() -> None:
    state = _state(_task("t1"), _task("t2"), _task("t3"))
    runner = RecordingRunner(failures={"t2": RuntimeError("CoinGecko 429")})

    await execute_plan(state, runner=runner, limits=_limits(), deadline_s=60.0)

    assert state.task_status == {
        "t1": TaskStatus.COMPLETED,
        "t2": TaskStatus.FAILED,
        "t3": TaskStatus.COMPLETED,
    }
    assert [finding.task_id for finding in state.findings] == ["t1", "t3"]


async def test_failure_emits_agent_failed_with_the_cause() -> None:
    state = _state(_task("t1"))
    runner = RecordingRunner(failures={"t1": RuntimeError("CoinGecko 429")})

    await execute_plan(state, runner=runner, limits=_limits(), deadline_s=60.0)
    events = await _drain(state.bus)

    failed = [event for event in events if event.type is EventType.AGENT_FAILED]
    assert len(failed) == 1
    error = _payload(failed[0], AgentFailedPayload).error
    assert error.code == "RuntimeError"
    assert "429" in error.message


async def test_timeout_is_reported_as_a_task_failure() -> None:
    state = _state(_task("t1"), _task("t2"))
    runner = RecordingRunner(delays={"t1": 5.0})

    await execute_plan(state, runner=runner, limits=_limits(task_timeout_s=0.02), deadline_s=60.0)

    assert state.task_status["t1"] is TaskStatus.FAILED
    assert state.task_status["t2"] is TaskStatus.COMPLETED
    events = await _drain(state.bus)
    failed = [event for event in events if event.type is EventType.AGENT_FAILED]
    assert _payload(failed[0], AgentFailedPayload).error.code == "task_timeout"


async def test_dependent_task_still_runs_after_its_dependency_failed() -> None:
    """跳过会把一次失败放大成整条链的失败，与 §7.2「不中断整个流程」相悖。"""
    state = _state(_task("t1"), _task("t2", depends_on=["t1"]))
    runner = RecordingRunner(failures={"t1": RuntimeError("挂了")})

    await execute_plan(state, runner=runner, limits=_limits(), deadline_s=60.0)

    assert runner.started == ["t1", "t2"]
    assert state.task_status["t2"] is TaskStatus.COMPLETED
    assert runner.contexts["t2"].missing_upstream == ("t1",)


async def test_missing_upstream_is_disclosed_as_a_data_gap() -> None:
    """不披露比缺数据更糟：读者会以为这一节是在完整信息下得出的。"""
    state = _state(_task("t1"), _task("t2", depends_on=["t1"]))
    runner = RecordingRunner(failures={"t1": RuntimeError("挂了")})

    await execute_plan(state, runner=runner, limits=_limits(), deadline_s=60.0)

    gaps = state.findings[0].data_gaps
    assert len(gaps) == 1
    assert "t1" in gaps[0]


# ─────────────────────────────────────────────────────────────────────────────
# 层边界的护栏
# ─────────────────────────────────────────────────────────────────────────────


async def test_exhausted_time_skips_remaining_layers() -> None:
    clock = FakeClock()
    state = _state(_task("t1"), _task("t2", depends_on=["t1"]), clock=clock)
    runner = RecordingRunner(clock=clock, burns=40.0)  # 第一层就把预算烧完

    await execute_plan(state, runner=runner, limits=_limits(), deadline_s=30.0)

    assert state.task_status["t1"] is TaskStatus.COMPLETED
    assert state.task_status["t2"] is TaskStatus.SKIPPED
    events = await _drain(state.bus)
    warnings = [event for event in events if event.type is EventType.WARNING]
    assert _payload(warnings[0], WarningPayload).code == "time_exhausted"


async def test_exhausted_budget_skips_remaining_layers() -> None:
    state = _state(_task("t1"), _task("t2", depends_on=["t1"]))
    state.cost_usd = 5.0  # 已超出下面设的 1.0 上限
    runner = RecordingRunner()

    await execute_plan(
        state, runner=runner, limits=_limits(max_session_cost_usd=1.0), deadline_s=60.0
    )

    assert runner.started == []
    assert set(state.task_status.values()) == {TaskStatus.SKIPPED}
    events = await _drain(state.bus)
    warnings = [event for event in events if event.type is EventType.WARNING]
    assert _payload(warnings[0], WarningPayload).code == "budget_exhausted"


async def test_skip_warning_is_aggregated_not_per_task() -> None:
    """跳过的原因是同一个，逐条发只会把事件流刷满而不增加信息。"""
    state = _state(_task("t1"), _task("t2", depends_on=["t1"]), _task("t3", depends_on=["t1"]))
    state.cost_usd = 5.0

    await execute_plan(
        state,
        runner=RecordingRunner(),
        limits=_limits(max_session_cost_usd=1.0),
        deadline_s=60.0,
    )
    events = await _drain(state.bus)

    assert len([event for event in events if event.type is EventType.WARNING]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 取消
# ─────────────────────────────────────────────────────────────────────────────


async def test_cancellation_propagates_instead_of_being_swallowed() -> None:
    """吞掉 CancelledError 会让整体超时与用户取消都失效——会话再也停不下来。"""
    state = _state(_task("t1"))
    runner = RecordingRunner(failures={"t1": asyncio.CancelledError()})

    with pytest.raises(asyncio.CancelledError):
        await execute_plan(state, runner=runner, limits=_limits(), deadline_s=60.0)

    assert state.task_status["t1"] is TaskStatus.SKIPPED


async def test_execute_plan_requires_a_plan() -> None:
    bus = EventBus("sess-1", heartbeat_interval_s=60.0)
    state = ResearchState("sess-1", "问题", bus=bus)

    with pytest.raises(RuntimeError, match="attach_plan"):
        await execute_plan(state, runner=RecordingRunner(), limits=_limits(), deadline_s=60.0)
