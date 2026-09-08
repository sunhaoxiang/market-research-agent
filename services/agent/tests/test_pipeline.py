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
from agents import Usage
from agents.testing import ScriptedModel, assistant_message
from pydantic import SecretStr

from agent_service.agents.report_writer import ReportWriterAgent, build_report_writer
from agent_service.agents.research_manager import PlannerAgent, build_research_manager
from agent_service.models.registry import ModelRegistry
from agent_service.observability.event_bus import EventBus
from agent_service.orchestrator.executor import TaskContext
from agent_service.orchestrator.pipeline import run_research
from agent_service.orchestrator.state import ResearchState
from agent_service.schemas.common import AgentName, QuestionType, SourceType, Stage, TaskStatus
from agent_service.schemas.entities import MetricPoint
from agent_service.schemas.events import (
    TERMINAL_EVENT_TYPES,
    AgentCompletedPayload,
    AgentRunMetricsEvent,
    AgentStartedPayload,
    ConflictDetectedPayload,
    EventType,
    IntentClassifiedPayload,
    ResearchEvent,
    SessionCompletedPayload,
    SessionFailedPayload,
    StageChangedPayload,
    TokenUsage,
)
from agent_service.schemas.findings import ResearchFinding
from agent_service.schemas.sources import Source
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


def _planner(
    *replies: str,
    limits: IsolatedExecutionLimits | None = None,
    per_call: Usage | None = None,
) -> PlannerAgent:
    registry = ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
        )
    )
    built = build_research_manager(registry, limits or _limits())
    scripted = ScriptedModel(
        [[assistant_message(reply)] for reply in replies],
        default_usage=per_call,
    )
    return PlannerAgent(
        agent=built.agent.clone(model=scripted),
        strategy=built.strategy,
        entry=built.entry,
        prompt=built.prompt,
    )


def _report_json(**overrides: Any) -> str:
    body: dict[str, Any] = {
        "title": "Hyperliquid 研究",
        "executive_summary": "协议收入相关数据仍不完整，当前仅有占位结论。",
        "sections": [
            {
                "id": "Overview",
                "title": "概述",
                "markdown": "研究尚未拿到完整的市场数据。",
                "claim_ids": [],
            },
            {
                "id": "Risks",
                "title": "风险",
                "markdown": "子 Agent 尚未实现，结论受限。",
                "claim_ids": [],
            },
        ],
        "data_gaps": [],
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


def _writer(*replies: str, per_call: Usage | None = None) -> ReportWriterAgent:
    registry = ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
        )
    )
    built = build_report_writer(registry)
    scripted = ScriptedModel(
        [[assistant_message(reply)] for reply in replies or (_report_json(),)],
        default_usage=per_call,
    )
    return ReportWriterAgent(
        agent=built.agent.clone(model=scripted),
        strategy=built.strategy,
        entry=built.entry,
        prompt=built.prompt,
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
        writer=_writer(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    assert outcome.succeeded
    assert runner.started == ["t1", "t2"]
    assert [finding.task_id for finding in outcome.findings] == ["t1", "t2"]
    assert set(outcome.state.task_status.values()) == {TaskStatus.COMPLETED}
    assert outcome.report is not None
    assert outcome.report.title


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
        writer=_writer(),
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
        EventType.AGENT_RUN_METRICS,  # planner
        EventType.STAGE_CHANGED,  # researching
        EventType.STAGE_CHANGED,  # writing
        EventType.REPORT_STARTED,
        EventType.AGENT_RUN_METRICS,  # report writer
        EventType.REPORT_COMPLETED,
        EventType.SESSION_COMPLETED,
    ]
    assert types.count(EventType.AGENT_STARTED) == 2
    assert types.count(EventType.AGENT_COMPLETED) == 2

    intent_i = types.index(EventType.INTENT_CLASSIFIED)
    plan_i = types.index(EventType.PLAN_CREATED)
    assert intent_i < plan_i
    intent = events[intent_i].payload
    assert isinstance(intent, IntentClassifiedPayload)
    assert intent.question_type is QuestionType.CRYPTO
    assert [entity.symbol for entity in intent.entities] == ["HYPE"]


async def test_every_task_is_started_before_it_completes() -> None:
    """前端按 task_id 点亮节点；先收到 completed 会让节点从「未开始」直接跳到「完成」。"""
    bus = _bus()
    await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json()),
        runner=StubRunner(),
        writer=_writer(),
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
        writer=_writer(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )
    stages = [
        _payload(event, StageChangedPayload)
        for event in await _drain(bus)
        if event.type is EventType.STAGE_CHANGED
    ]

    assert [payload.stage for payload in stages] == [
        Stage.PLANNING,
        Stage.RESEARCHING,
        Stage.WRITING,
    ]
    assert stages[0].previous is None
    assert stages[1].previous is Stage.PLANNING
    assert stages[2].previous is Stage.RESEARCHING


async def test_planner_usage_is_accounted() -> None:
    """成本护栏要在下一层任务启动前生效，所以规划的用量必须当场入账（§20.1）。"""
    bus = _bus()
    outcome = await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json()),
        runner=StubRunner(),
        writer=_writer(),
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
        writer=_writer(),
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
        writer=_writer(),
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
        writer=_writer(),
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
        writer=_writer(),
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


async def test_planner_run_is_metered_even_when_the_plan_is_rejected() -> None:
    """失败路径也要记账（§20.1）。

    模型调用本身成功了，钱已经花掉，只是产物不可用。只在成功时记录会让
    `/debug` 里的成本系统性偏低，而偏低的幅度正好集中在最该被关注的
    那些会话——"反复重试后失败"往往就是最贵的一次。
    """
    bus = _bus()
    await run_research(
        "你好",
        planner=_planner(_plan_json(tasks=[]), per_call=Usage(input_tokens=800, output_tokens=200)),
        runner=StubRunner(),
        writer=_writer(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    metrics = [e for e in await _drain(bus) if isinstance(e, AgentRunMetricsEvent)]
    assert len(metrics) == 1
    assert metrics[0].payload.status is TaskStatus.FAILED
    assert metrics[0].payload.error is not None
    assert metrics[0].payload.error.code == "plan_rejected"
    assert metrics[0].payload.usage == TokenUsage(input=800, output=200)


async def test_planner_run_records_prompt_and_model() -> None:
    """`agent_runs` 要能回答"这次用了哪个模型、哪版 prompt"（§20.1）。"""
    bus = _bus()
    await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json(), per_call=Usage(input_tokens=1000, output_tokens=300)),
        runner=StubRunner(),
        writer=_writer(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    (metrics,) = [
        e
        for e in await _drain(bus)
        if isinstance(e, AgentRunMetricsEvent) and e.payload.agent is AgentName.RESEARCH_MANAGER
    ]
    assert metrics.payload.agent is AgentName.RESEARCH_MANAGER
    assert metrics.payload.model_id == "deepseek:deepseek-v4-pro"
    # 规划不属于计划里的任何任务，所以没有 task_id
    assert metrics.payload.task_id is None
    assert metrics.payload.prompt is not None
    assert metrics.payload.prompt.chars > 1000
    assert metrics.payload.duration_ms >= 0


async def test_retried_planning_reports_the_total_usage() -> None:
    """规划重试烧掉的 token 要全部计入会话成本。"""
    bus = _bus()
    await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(
            "这不是 JSON",
            _plan_json(),
            per_call=Usage(input_tokens=1000, output_tokens=300),
        ),
        runner=StubRunner(),
        writer=_writer(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    (metrics,) = [
        e
        for e in await _drain(bus)
        if isinstance(e, AgentRunMetricsEvent) and e.payload.agent is AgentName.RESEARCH_MANAGER
    ]
    assert metrics.payload.usage == TokenUsage(input=2000, output=600)


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
        writer=_writer(),
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
        writer=_writer(),
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
            writer=_writer(),
            limits=_limits(),
            bus=bus,
            now=_NOW,
        )

    events = await _drain(bus)
    assert events[-1].type is EventType.SESSION_CANCELLED


async def test_report_writer_failure_fails_the_session() -> None:
    """§7.2：Report Writer 没有降级形态。"""
    bus = _bus()
    outcome = await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json()),
        runner=StubRunner(),
        writer=_writer("这不是 JSON", "还不是", "仍然不是"),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    assert not outcome.succeeded
    assert outcome.report is None
    events = await _drain(bus)
    assert events[-1].type is EventType.SESSION_FAILED
    assert _payload(events[-1], SessionFailedPayload).stage is Stage.WRITING
    assert _payload(events[-1], SessionFailedPayload).error.code == "structured_output"


async def test_injected_metric_conflict_emits_conflict_detected() -> None:
    """验收：注入冲突数据能被检出，事件出现在撰写之前。"""
    cg = Source(
        ref="s1",
        url="https://www.coingecko.com/en/coins/hyperliquid",
        url_canonical="https://www.coingecko.com/en/coins/hyperliquid",
        title="CoinGecko",
        domain="coingecko.com",
        source_type=SourceType.API,
        provider="coingecko",
        retrieved_at=_NOW,
    )
    llama = Source(
        ref="s2",
        url="https://defillama.com/protocol/hyperliquid",
        url_canonical="https://defillama.com/protocol/hyperliquid",
        title="DefiLlama",
        domain="defillama.com",
        source_type=SourceType.API,
        provider="defillama",
        retrieved_at=_NOW,
    )

    @dataclass
    class InjectedRunner:
        def model_id_for(self, agent: AgentName) -> str:
            del agent
            return "deepseek:deepseek-v4-flash"

        async def run(self, context: TaskContext, state: ResearchState) -> ResearchFinding:
            del state
            if context.task.id != "t1":
                return ResearchFinding(
                    task_id=context.task.id,
                    agent=context.task.agent,
                    summary=f"{context.task.id} 的结论",
                )
            return ResearchFinding(
                task_id=context.task.id,
                agent=context.task.agent,
                summary="TVL 来自两个数据源。",
                sources=[cg, llama],
                metrics=[
                    MetricPoint(
                        name="tvl",
                        label="TVL",
                        value=1.2e9,
                        unit="USD",
                        entity_symbol="HYPE",
                        as_of=_NOW,
                        source_ref="s1",
                    ),
                    MetricPoint(
                        name="tvl",
                        label="TVL",
                        value=1.8e9,
                        unit="USD",
                        entity_symbol="HYPE",
                        as_of=_NOW,
                        source_ref="s2",
                    ),
                ],
            )

    bus = _bus()
    outcome = await run_research(
        "查询 HYPE 的 TVL",
        planner=_planner(_plan_json()),
        runner=InjectedRunner(),
        writer=_writer(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    assert outcome.succeeded
    assert len(outcome.state.conflicts) == 1
    events = await _drain(bus)
    types = [event.type for event in events]
    detected = [event for event in events if event.type is EventType.CONFLICT_DETECTED]
    assert len(detected) == 1
    conflict = _payload(detected[0], ConflictDetectedPayload).conflict
    assert conflict.values == ["coingecko: 1.2e+09 USD", "defillama: 1.8e+09 USD"]
    assert types.index(EventType.CONFLICT_DETECTED) > types.index(EventType.AGENT_COMPLETED)
    writing = next(
        i
        for i, event in enumerate(events)
        if event.type is EventType.STAGE_CHANGED
        and _payload(event, StageChangedPayload).stage is Stage.WRITING
    )
    assert types.index(EventType.CONFLICT_DETECTED) < writing
