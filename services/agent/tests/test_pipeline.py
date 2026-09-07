"""会话全流程（P1-10 验收）。

规划走真实的 `Runner` + `ScriptedModel`，执行走测试替身 runner——
子 Agent 是 Phase 2-4 的内容，现在能验的是编排：阶段顺序、事件完整性、
终态唯一性、失败与取消的传播。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from agents.testing import ScriptedModel, assistant_message
from pydantic import SecretStr

from agent_service.agents.research_manager import PlannerAgent, build_research_manager
from agent_service.models.registry import ModelRegistry
from agent_service.observability.event_bus import EventBus
from agent_service.orchestrator.executor import TaskContext
from agent_service.orchestrator.pipeline import run_research
from agent_service.orchestrator.state import ResearchState
from agent_service.schemas.common import AgentName, Stage, TaskStatus
from agent_service.schemas.events import (
    TERMINAL_EVENT_TYPES,
    AgentCompletedPayload,
    AgentStartedPayload,
    EventType,
    ResearchEvent,
    SessionCompletedPayload,
    SessionFailedPayload,
    StageChangedPayload,
)
from agent_service.schemas.findings import ResearchFinding
from agent_service.testing import (
    IsolatedExecutionLimits,
    IsolatedProviderCredentials,
    IsolatedSettings,
)

_NOW = datetime(2026, 9, 7, tzinfo=UTC)


def _plan_json(**overrides: Any) -> str:
    body: dict[str, Any] = {
        "question_type": "crypto",
        "interpretation": "用户想了解 Hyperliquid 的协议收入",
        "entities": [],
        "tasks": [
            {
                "id": "t1",
                "agent": "crypto_research",
                "objective": "获取 Hyperliquid 过去 90 天的手续费收入",
                "entities": [],
                "suggested_tools": [],
                "depends_on": [],
                "priority": 0,
            },
            {
                "id": "t2",
                "agent": "web_research",
                "objective": "查找 HYPE 的代币解锁时间表",
                "entities": [],
                "suggested_tools": [],
                "depends_on": [],
                "priority": 0,
            },
        ],
        "report_sections": ["Overview", "Risks"],
        "assumptions": [],
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


def _limits(**overrides: float | int) -> IsolatedExecutionLimits:
    return IsolatedExecutionLimits(**overrides)  # pyright: ignore[reportArgumentType]


def _planner(*replies: str, limits: IsolatedExecutionLimits | None = None) -> PlannerAgent:
    registry = ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
        )
    )
    built = build_research_manager(registry, limits or _limits())
    scripted = ScriptedModel([[assistant_message(reply)] for reply in replies])
    return PlannerAgent(
        agent=built.agent.clone(model=scripted),
        strategy=built.strategy,
        entry=built.entry,
    )


@dataclass
class StubRunner:
    """最小可用的 TaskRunner。"""

    failures: dict[str, BaseException] = field(default_factory=dict)
    started: list[str] = field(default_factory=list)

    def model_id_for(self, agent: AgentName) -> str:
        del agent
        return "deepseek:deepseek-v4-flash"

    async def run(self, context: TaskContext, state: ResearchState) -> ResearchFinding:
        del state
        self.started.append(context.task.id)
        if context.task.id in self.failures:
            raise self.failures[context.task.id]
        return ResearchFinding(
            task_id=context.task.id,
            agent=context.task.agent,
            summary=f"{context.task.id} 的结论",
        )


def _bus() -> EventBus:
    return EventBus("sess-1", heartbeat_interval_s=60.0)


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


async def test_full_run_reaches_completion() -> None:
    bus = _bus()
    runner = StubRunner()

    outcome = await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json()),
        runner=runner,
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    assert outcome.succeeded
    assert runner.started == ["t1", "t2"]
    assert [finding.task_id for finding in outcome.findings] == ["t1", "t2"]
    assert set(outcome.state.task_status.values()) == {TaskStatus.COMPLETED}


async def test_event_sequence_covers_the_whole_flow() -> None:
    """前端 reducer 依赖这个骨架顺序：会话开始 → 阶段 → 理解 → 计划 → 阶段 → 任务 → 终态。

    刻意不断言同层任务之间的交错（`t1` 的 completed 与 `t2` 的 started 谁先），
    那由事件循环的调度决定，写死等于把替身的实现细节当成契约。
    """
    bus = _bus()
    await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json()),
        runner=StubRunner(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )
    events = await _drain(bus)
    types = [event.type for event in events]

    task_events = {EventType.AGENT_STARTED, EventType.AGENT_COMPLETED}
    assert [event for event in types if event not in task_events] == [
        EventType.SESSION_STARTED,
        EventType.STAGE_CHANGED,  # planning
        EventType.INTENT_CLASSIFIED,
        EventType.PLAN_CREATED,
        EventType.STAGE_CHANGED,  # researching
        EventType.SESSION_COMPLETED,
    ]
    assert types.count(EventType.AGENT_STARTED) == 2
    assert types.count(EventType.AGENT_COMPLETED) == 2


async def test_every_task_is_started_before_it_completes() -> None:
    """前端按 task_id 点亮节点；先收到 completed 会让节点从「未开始」直接跳到「完成」。"""
    bus = _bus()
    await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json()),
        runner=StubRunner(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    seen: set[str] = set()
    for event in await _drain(bus):
        payload = event.payload
        if event.type is EventType.AGENT_STARTED:
            assert isinstance(payload, AgentStartedPayload)
            seen.add(payload.task_id)
        elif event.type is EventType.AGENT_COMPLETED:
            assert isinstance(payload, AgentCompletedPayload)
            assert payload.task_id in seen


async def test_stages_advance_in_order() -> None:
    bus = _bus()
    await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json()),
        runner=StubRunner(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )
    stages = [
        _payload(event, StageChangedPayload)
        for event in await _drain(bus)
        if event.type is EventType.STAGE_CHANGED
    ]

    assert [payload.stage for payload in stages] == [Stage.PLANNING, Stage.RESEARCHING]
    assert stages[0].previous is None
    assert stages[1].previous is Stage.PLANNING


async def test_planner_usage_is_accounted() -> None:
    """成本护栏要在下一层任务启动前生效，所以规划的用量必须当场入账（§20.1）。"""
    bus = _bus()
    outcome = await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json()),
        runner=StubRunner(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    completed = [event for event in await _drain(bus) if event.type is EventType.SESSION_COMPLETED]
    payload = _payload(completed[0], SessionCompletedPayload)
    assert payload.usage == outcome.state.usage
    assert payload.duration_ms >= 0


async def test_session_id_is_generated_when_not_supplied() -> None:
    outcome = await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json()),
        runner=StubRunner(),
        limits=_limits(),
        bus=_bus(),
        now=_NOW,
    )

    assert outcome.session_id


# ─────────────────────────────────────────────────────────────────────────────
# 失败路径
# ─────────────────────────────────────────────────────────────────────────────


async def test_task_failure_still_completes_the_session() -> None:
    """§7.2：只有 Planner 与 Report Writer 失败才导致整体失败。"""
    bus = _bus()
    outcome = await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json()),
        runner=StubRunner(failures={"t1": RuntimeError("上游 429")}),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    assert outcome.succeeded
    assert outcome.state.task_status["t1"] is TaskStatus.FAILED
    types = [event.type for event in await _drain(bus)]
    assert EventType.AGENT_FAILED in types
    assert types[-1] is EventType.SESSION_COMPLETED


async def test_completion_message_discloses_partial_results() -> None:
    """用户看到 2 个任务的计划却只拿到 1 份结果时，需要知道那不是 bug。"""
    bus = _bus()
    await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json()),
        runner=StubRunner(failures={"t1": RuntimeError("上游 429")}),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    completed = [event for event in await _drain(bus) if event.type is EventType.SESSION_COMPLETED]
    assert completed[0].message == "研究完成，1/2 个任务成功（其余失败或跳过，详见数据限制）"


async def test_rejected_plan_fails_the_session() -> None:
    bus = _bus()
    outcome = await run_research(
        "你好",
        planner=_planner(_plan_json(tasks=[])),
        runner=StubRunner(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    assert not outcome.succeeded
    events = await _drain(bus)
    assert events[-1].type is EventType.SESSION_FAILED
    payload = _payload(events[-1], SessionFailedPayload)
    assert payload.error.code == "plan_rejected"
    assert payload.stage is Stage.PLANNING  # 前端据此知道是在哪一步挂的


async def test_unexpected_error_still_emits_a_terminal_event() -> None:
    """漏发终态事件的后果是前端永远转圈，比报错更糟。"""

    class ExplodingRunner(StubRunner):
        def model_id_for(self, agent: AgentName) -> str:
            del agent
            raise ValueError("registry 配置坏了")

    bus = _bus()
    outcome = await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json()),
        runner=ExplodingRunner(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    assert not outcome.succeeded
    events = await _drain(bus)
    assert events[-1].type is EventType.SESSION_FAILED
    assert _payload(events[-1], SessionFailedPayload).error.code == "ValueError"


async def test_exactly_one_terminal_event_is_emitted() -> None:
    """总线在终态事件后即关闭；发两个的话第二个会直接抛 BusClosedError。"""
    bus = _bus()
    await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json()),
        runner=StubRunner(failures={"t1": RuntimeError("挂了")}),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )
    events = await _drain(bus)

    terminal = [event for event in events if event.type in TERMINAL_EVENT_TYPES]
    assert len(terminal) == 1


async def test_cancellation_emits_cancelled_and_propagates() -> None:
    bus = _bus()

    with pytest.raises(asyncio.CancelledError):
        await run_research(
            "Hyperliquid 怎么样？",
            planner=_planner(_plan_json()),
            runner=StubRunner(failures={"t1": asyncio.CancelledError()}),
            limits=_limits(),
            bus=bus,
            now=_NOW,
        )

    events = await _drain(bus)
    assert events[-1].type is EventType.SESSION_CANCELLED
