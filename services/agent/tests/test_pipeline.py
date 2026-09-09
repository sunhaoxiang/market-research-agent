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
from agent_service.schemas.claims import Claim
from agent_service.schemas.common import (
    AgentName,
    ConfidenceLevel,
    EpistemicType,
    QuestionType,
    SourceType,
    Stage,
    TaskStatus,
)
from agent_service.schemas.entities import MetricPoint
from agent_service.schemas.events import (
    TERMINAL_EVENT_TYPES,
    AgentCompletedPayload,
    AgentRunMetricsEvent,
    AgentStartedPayload,
    ConflictDetectedPayload,
    EventType,
    IntentClassifiedPayload,
    PlanUpdatedPayload,
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
    delays: dict[str, float] = field(default_factory=dict)
    results: dict[str, ResearchFinding] = field(default_factory=dict)
    salvages: dict[str, ResearchFinding] = field(default_factory=dict)
    started: list[str] = field(default_factory=list)

    def model_id_for(self, agent: AgentName) -> str:
        del agent
        return "deepseek:deepseek-v4-flash"

    async def run(self, context: TaskContext, state: ResearchState) -> ResearchFinding:
        del state
        self.started.append(context.task.id)
        delay = self.delays.get(context.task.id, 0.0)
        if delay:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                salvage = self.salvages.get(context.task.id)
                if salvage is not None:
                    context.salvage.finding = salvage
                raise
        if context.task.id in self.failures:
            raise self.failures[context.task.id]
        if context.task.id in self.results:
            return self.results[context.task.id]
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
        EventType.USAGE_UPDATED,
        EventType.STAGE_CHANGED,  # researching
        EventType.STAGE_CHANGED,  # writing
        EventType.REPORT_STARTED,
        EventType.AGENT_RUN_METRICS,  # report writer
        EventType.USAGE_UPDATED,
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


async def test_timeout_salvage_metrics_reach_the_comparison_table() -> None:
    """一层里一只超时仍出报告，salvage 数字能进对比表（P5-3 / D22）。"""

    def _revenue(task_id: str, symbol: str, value: float) -> ResearchFinding:
        return ResearchFinding(
            task_id=task_id,
            agent=AgentName.STOCK_RESEARCH,
            summary=f"{symbol} 基本面",
            metrics=[
                MetricPoint(
                    name="revenue",
                    label="营收",
                    value=value,
                    unit="USD",
                    entity_symbol=symbol,
                )
            ],
        )

    plan = _plan_json(
        question_type="compare",
        interpretation="比较 NVDA、AMD、AVGO 基本面",
        tasks=[
            {
                "id": "t1",
                "agent": "stock_research",
                "objective": "取 NVDA 最近一季营收",
                "entities": [],
                "suggested_tools": [],
                "depends_on": [],
                "priority": 0,
            },
            {
                "id": "t2",
                "agent": "stock_research",
                "objective": "取 AMD 最近一季营收",
                "entities": [],
                "suggested_tools": [],
                "depends_on": [],
                "priority": 0,
            },
            {
                "id": "t3",
                "agent": "stock_research",
                "objective": "取 AVGO 最近一季营收",
                "entities": [],
                "suggested_tools": [],
                "depends_on": [],
                "priority": 0,
            },
        ],
        report_sections=["Comparison"],
    )
    report = _report_json(
        title="NVDA / AMD / AVGO 对比",
        executive_summary="三家规模不同。",
        sections=[
            {
                "id": "Comparison",
                "title": "对比",
                "markdown": "以下为基本面对照。",
                "claim_ids": [],
            }
        ],
    )
    bus = _bus()
    outcome = await run_research(
        "比较 NVDA、AMD、AVGO",
        planner=_planner(plan),
        runner=StubRunner(
            delays={"t3": 5.0},
            results={
                "t1": _revenue("t1", "NVDA", 46_743_000_000.0),
                "t2": _revenue("t2", "AMD", 7_400_000_000.0),
            },
            salvages={"t3": _revenue("t3", "AVGO", 15_952_000_000.0)},
        ),
        writer=_writer(report),
        limits=_limits(task_timeout_s=0.05),
        bus=bus,
        now=_NOW,
    )

    assert outcome.succeeded
    assert outcome.state.task_status["t3"] is TaskStatus.FAILED
    assert outcome.report is not None
    markdown = outcome.report.sections[0].markdown
    assert "| 指标 | NVDA | AMD | AVGO |" in markdown
    assert "15,952,000,000" in markdown


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


async def test_duplicate_sources_across_agents_are_merged() -> None:
    """验收：跨 Agent 的重复来源被合并（P5-4）。"""
    crypto_src = Source(
        ref="s1",
        url="https://www.theblock.co/hyperliquid-fee-share?utm_source=x",
        url_canonical="https://theblock.co/hyperliquid-fee-share",
        title=None,
        domain="theblock.co",
        source_type=SourceType.NEWS,
        provider="tavily",
        retrieved_at=_NOW,
    )
    web_src = Source(
        ref="s1",
        url="https://www.theblock.co/hyperliquid-fee-share/",
        url_canonical="https://theblock.co/hyperliquid-fee-share",
        title="Fee share",
        domain="theblock.co",
        source_type=SourceType.NEWS,
        provider="tavily",
        retrieved_at=_NOW,
        excerpt="讨论手续费分成。",
    )
    text = "HYPE 正在讨论手续费分成。"

    @dataclass
    class OverlapRunner:
        def model_id_for(self, agent: AgentName) -> str:
            del agent
            return "deepseek:deepseek-v4-flash"

        async def run(self, context: TaskContext, state: ResearchState) -> ResearchFinding:
            del state
            if context.task.id == "t1":
                return ResearchFinding(
                    task_id="t1",
                    agent=context.task.agent,
                    summary="链上侧看到手续费分成讨论。",
                    sources=[crypto_src],
                    claims=[
                        Claim(
                            text=text,
                            epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
                            confidence=ConfidenceLevel.MEDIUM,
                            source_ids=[crypto_src.id],
                        )
                    ],
                )
            return ResearchFinding(
                task_id=context.task.id,
                agent=context.task.agent,
                summary="网页侧也报道了手续费分成。",
                sources=[web_src],
                claims=[
                    Claim(
                        text=text,
                        epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
                        confidence=ConfidenceLevel.HIGH,
                        source_ids=[web_src.id],
                    )
                ],
            )

    outcome = await run_research(
        "Hyperliquid 怎么样？",
        planner=_planner(_plan_json()),
        runner=OverlapRunner(),
        writer=_writer(),
        limits=_limits(),
        bus=_bus(),
        now=_NOW,
    )

    assert outcome.succeeded
    catalog = outcome.state.source_registry.sources()
    assert len(catalog) == 1
    assert catalog[0].title == "Fee share"
    survivor = catalog[0].id
    by_task = {item.task_id: item for item in outcome.findings}
    assert by_task["t1"].claims[0].source_ids == [survivor]
    assert by_task["t1"].claims[0].confidence is ConfidenceLevel.HIGH
    assert by_task["t2"].claims == []
    assert {item.id for finding in outcome.findings for item in finding.sources} == {survivor}


async def test_fillable_gap_triggers_one_supplement_round() -> None:
    """验收：缺关键数据时能补一轮，事件流有 PLAN_UPDATED。"""
    tvl = MetricPoint(name="tvl", label="TVL", value=1.2e9, unit="USD", entity_symbol="HYPE")
    bus = _bus()
    runner = StubRunner(
        results={
            "t1": ResearchFinding(
                task_id="t1",
                agent=AgentName.CRYPTO_RESEARCH,
                summary="缺 TVL",
                data_gaps=["未能获取 HYPE 的 TVL"],
            ),
            "t3": ResearchFinding(
                task_id="t3",
                agent=AgentName.WEB_RESEARCH,
                summary="补到了 TVL。",
                metrics=[tvl],
            ),
        }
    )
    outcome = await run_research(
        "查询 HYPE 的 TVL",
        planner=_planner(_plan_json()),
        runner=runner,
        writer=_writer(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    assert outcome.succeeded
    assert "t3" in runner.started
    assert {finding.task_id for finding in outcome.findings} >= {"t1", "t2", "t3"}
    by_id = {finding.task_id: finding for finding in outcome.findings}
    assert by_id["t3"].metrics[0].value == 1.2e9

    events = await _drain(bus)
    updates = [event for event in events if event.type is EventType.PLAN_UPDATED]
    assert len(updates) == 1
    added = _payload(updates[0], PlanUpdatedPayload).added_tasks
    assert added[0].id == "t3"
    assert added[0].agent is AgentName.WEB_RESEARCH
    started_ids = [
        _payload(event, AgentStartedPayload).task_id
        for event in events
        if event.type is EventType.AGENT_STARTED
    ]
    assert started_ids.count("t3") == 1
    assert started_ids.index("t3") > started_ids.index("t1")


async def test_second_gap_does_not_start_another_round() -> None:
    bus = _bus()
    runner = StubRunner(
        results={
            "t1": ResearchFinding(
                task_id="t1",
                agent=AgentName.CRYPTO_RESEARCH,
                summary="缺 TVL",
                data_gaps=["未能获取 HYPE 的 TVL"],
            ),
            "t3": ResearchFinding(
                task_id="t3",
                agent=AgentName.WEB_RESEARCH,
                summary="还是缺",
                data_gaps=["未找到 DefiLlama 页面"],
            ),
        }
    )
    await run_research(
        "查询 HYPE 的 TVL",
        planner=_planner(_plan_json()),
        runner=runner,
        writer=_writer(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )
    events = await _drain(bus)
    assert sum(1 for event in events if event.type is EventType.PLAN_UPDATED) == 1
    assert runner.started.count("t3") == 1
    assert "t4" not in runner.started


async def test_unfillable_gap_does_not_supplement() -> None:
    bus = _bus()
    await run_research(
        "查询 HYPE 的 TVL",
        planner=_planner(_plan_json()),
        runner=StubRunner(
            results={
                "t1": ResearchFinding(
                    task_id="t1",
                    agent=AgentName.CRYPTO_RESEARCH,
                    summary="占位",
                    data_gaps=["子 Agent 尚未实现（Phase 2-4），本任务未获取任何真实数据"],
                )
            }
        ),
        writer=_writer(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )
    events = await _drain(bus)
    assert EventType.PLAN_UPDATED not in [event.type for event in events]


async def test_max_supplement_rounds_zero_skips() -> None:
    bus = _bus()
    runner = StubRunner(
        results={
            "t1": ResearchFinding(
                task_id="t1",
                agent=AgentName.CRYPTO_RESEARCH,
                summary="缺 TVL",
                data_gaps=["未能获取 HYPE 的 TVL"],
            )
        }
    )
    await run_research(
        "查询 HYPE 的 TVL",
        planner=_planner(_plan_json()),
        runner=runner,
        writer=_writer(),
        limits=_limits(max_supplement_rounds=0),
        bus=bus,
        now=_NOW,
    )
    assert "t3" not in runner.started
    events = await _drain(bus)
    assert EventType.PLAN_UPDATED not in [event.type for event in events]
