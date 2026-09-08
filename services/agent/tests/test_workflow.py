"""全流程 workflow（P5-8 / [DP §18.2]）。

planning → fan-out → merge → fact check → report。Planner / Writer / Fact
Checker 走 ScriptedModel；子任务用替身 runner 注入失败、超时与冲突——
这些路径用真工具既贵又不稳定。`max_supplement_rounds=0`，避免缺口检测
把六条路径搅成补充轮。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agents import Usage
from agents.testing import ScriptedModel, assistant_message
from pydantic import SecretStr

from agent_service.agents.fact_checker import FactCheckerAgent, build_fact_checker
from agent_service.agents.report_writer import ReportWriterAgent, build_report_writer
from agent_service.agents.research_manager import PlannerAgent, build_research_manager
from agent_service.models.registry import ModelRegistry
from agent_service.observability.event_bus import EventBus
from agent_service.orchestrator.executor import TaskContext
from agent_service.orchestrator.pipeline import ResearchOutcome, run_research
from agent_service.orchestrator.state import ResearchState
from agent_service.schemas.claims import Claim
from agent_service.schemas.common import (
    AgentName,
    ConfidenceLevel,
    EpistemicType,
    SourceType,
    Stage,
    TaskStatus,
)
from agent_service.schemas.entities import MetricPoint
from agent_service.schemas.events import (
    ConflictDetectedPayload,
    EventType,
    ResearchEvent,
    SessionFailedPayload,
    StageChangedPayload,
    TokenUsage,
    WarningEvent,
)
from agent_service.schemas.findings import ResearchFinding
from agent_service.schemas.sources import Source
from agent_service.sources.guardrail import CITATION_WARNING_CODE
from agent_service.testing import (
    IsolatedExecutionLimits,
    IsolatedProviderCredentials,
    IsolatedSettings,
)

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_CLAIM_ID = "claim-hype-fee-share"
_URL = "https://theblock.co/hyperliquid-fee-share"


def _registry() -> ModelRegistry:
    return ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
        )
    )


def _limits(**overrides: float | int) -> IsolatedExecutionLimits:
    body: dict[str, float | int] = {"max_supplement_rounds": 0}
    body.update(overrides)
    return IsolatedExecutionLimits(**body)  # pyright: ignore[reportArgumentType]


def _plan_json(**overrides: Any) -> str:
    body: dict[str, Any] = {
        "question_type": "crypto",
        "interpretation": "用户想了解 Hyperliquid 的协议收入与近期进展",
        "entities": [{"type": "crypto", "symbol": "HYPE", "name": "Hyperliquid"}],
        "tasks": [
            {
                "id": "t1",
                "agent": "crypto_research",
                "objective": "获取 Hyperliquid 过去 90 天的手续费收入",
                "entities": [{"type": "crypto", "symbol": "HYPE", "name": "Hyperliquid"}],
                "suggested_tools": [],
                "depends_on": [],
                "priority": 1,
            },
            {
                "id": "t2",
                "agent": "web_research",
                "objective": "查找 HYPE 最近的重要新闻",
                "entities": [{"type": "crypto", "symbol": "HYPE", "name": "Hyperliquid"}],
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


def _source() -> Source:
    return Source(
        ref="s1",
        url=_URL,
        url_canonical=_URL,
        title="Fee share",
        domain="theblock.co",
        source_type=SourceType.NEWS,
        retrieved_at=_NOW,
        excerpt="The protocol may share trading fees with HYPE holders.",
    )


def _backed_finding(source: Source, *, task_id: str = "t1") -> ResearchFinding:
    return ResearchFinding(
        task_id=task_id,
        agent=AgentName.CRYPTO_RESEARCH,
        summary="近期有手续费分享讨论。",
        claims=[
            Claim(
                id=_CLAIM_ID,
                text="Hyperliquid 正在讨论将部分交易手续费分享给 HYPE 持有人。",
                epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
                confidence=ConfidenceLevel.MEDIUM,
                source_ids=[source.id],
                task_id=task_id,
                agent=AgentName.CRYPTO_RESEARCH.value,
            )
        ],
        sources=[source],
    )


def _report_json(*, citation: str = "", **overrides: Any) -> str:
    body: dict[str, Any] = {
        "title": "Hyperliquid 研究",
        "executive_summary": f"协议近期在讨论手续费分享。{citation}",
        "sections": [
            {
                "id": "Overview",
                "title": "概述",
                "markdown": f"Hyperliquid 正在讨论把部分手续费分享给 HYPE 持有人。{citation}",
                "claim_ids": [],
            }
        ],
        "data_gaps": [],
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


def _check_json(*, verification: str = "verified") -> str:
    return json.dumps(
        {
            "verifications": [
                {
                    "claim_id": _CLAIM_ID,
                    "verification": verification,
                    "note": "来源支持该陈述。",
                    "confidence_adjustment": None,
                    "additional_source_refs": [],
                }
            ],
            "conflicts": [],
            "notes": [],
        },
        ensure_ascii=False,
    )


def _idle_check_json() -> str:
    return json.dumps({"verifications": [], "conflicts": [], "notes": []}, ensure_ascii=False)


def _planner(*replies: str, per_call: Usage | None = None) -> PlannerAgent:
    built = build_research_manager(_registry(), _limits())
    scripted = ScriptedModel(
        [[assistant_message(reply)] for reply in replies or (_plan_json(),)],
        default_usage=per_call,
    )
    return PlannerAgent(
        agent=built.agent.clone(model=scripted),
        strategy=built.strategy,
        entry=built.entry,
        prompt=built.prompt,
    )


def _writer(*replies: str) -> ReportWriterAgent:
    built = build_report_writer(_registry())
    scripted = ScriptedModel([[assistant_message(reply)] for reply in replies or (_report_json(),)])
    return ReportWriterAgent(
        agent=built.agent.clone(model=scripted),
        strategy=built.strategy,
        entry=built.entry,
        prompt=built.prompt,
    )


def _checker(*replies: str) -> FactCheckerAgent:
    built = build_fact_checker(_registry())
    scripted = ScriptedModel(
        [[assistant_message(reply)] for reply in replies or (_idle_check_json(),)]
    )
    return FactCheckerAgent(
        agent=built.agent.clone(model=scripted),
        strategy=built.strategy,
        entry=built.entry,
        prompt=built.prompt,
    )


@dataclass
class StubRunner:
    findings: dict[str, ResearchFinding] = field(default_factory=dict)
    failures: dict[str, BaseException] = field(default_factory=dict)
    delays: dict[str, float] = field(default_factory=dict)
    salvages: dict[str, ResearchFinding] = field(default_factory=dict)

    def model_id_for(self, agent: AgentName) -> str:
        del agent
        return "deepseek:deepseek-v4-flash"

    async def run(self, context: TaskContext, state: ResearchState) -> ResearchFinding:
        del state
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
        found = self.findings.get(context.task.id)
        if found is not None:
            return found.model_copy(
                update={"task_id": context.task.id, "agent": context.task.agent}
            )
        return ResearchFinding(
            task_id=context.task.id,
            agent=context.task.agent,
            summary=f"{context.task.id} 的结论",
        )


async def _drain(bus: EventBus) -> list[ResearchEvent]:
    bus.close()
    return [event async for event in bus.stream()]


def _payload[T](event: ResearchEvent, payload_type: type[T]) -> T:
    payload = event.payload
    assert isinstance(payload, payload_type)
    return payload


def _stages(events: list[ResearchEvent]) -> list[Stage]:
    return [
        _payload(event, StageChangedPayload).stage
        for event in events
        if event.type is EventType.STAGE_CHANGED
    ]


async def _workflow(
    question: str,
    *,
    planner: PlannerAgent | None = None,
    runner: StubRunner | None = None,
    writer: ReportWriterAgent | None = None,
    checker: FactCheckerAgent | None = None,
    limits: IsolatedExecutionLimits | None = None,
) -> tuple[ResearchOutcome, list[ResearchEvent]]:
    bus = EventBus("sess-p5-8", heartbeat_interval_s=60.0)
    outcome = await run_research(
        question,
        planner=planner or _planner(),
        runner=runner or StubRunner(),
        writer=writer or _writer(),
        fact_checker=checker or _checker(),
        limits=limits or _limits(),
        bus=bus,
        now=_NOW,
    )
    return outcome, await _drain(bus)


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


def _compare_plan() -> str:
    tasks = [
        {
            "id": f"t{index}",
            "agent": "stock_research",
            "objective": f"取 {symbol} 最近一季营收",
            "entities": [{"type": "stock", "symbol": symbol, "name": symbol}],
            "suggested_tools": [],
            "depends_on": [],
            "priority": 0,
        }
        for index, symbol in enumerate(("NVDA", "AMD", "AVGO"), start=1)
    ]
    return _plan_json(
        question_type="compare",
        interpretation="比较三家半导体公司基本面",
        entities=[
            {"type": "stock", "symbol": symbol, "name": symbol}
            for symbol in ("NVDA", "AMD", "AVGO")
        ],
        tasks=tasks,
        report_sections=["Comparison"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 六条路径
# ─────────────────────────────────────────────────────────────────────────────


async def test_workflow_happy_path() -> None:
    """正常：两任务完成，进入核查，产出带引用和免责声明的报告。"""
    source = _source()
    outcome, events = await _workflow(
        "Hyperliquid 怎么样？",
        runner=StubRunner(findings={"t1": _backed_finding(source)}),
        checker=_checker(_check_json()),
        writer=_writer(_report_json(citation="[1]")),
    )
    assert outcome.succeeded
    assert set(outcome.state.task_status.values()) == {TaskStatus.COMPLETED}
    assert outcome.report is not None
    assert "[1]" in outcome.report.executive_summary
    assert outcome.report.sections[-1].id == "Disclaimer"
    types = [event.type for event in events]
    assert types[-1] is EventType.SESSION_COMPLETED
    assert EventType.FACT_CHECK_STARTED in types
    assert EventType.CLAIM_VERIFIED in types
    assert EventType.PLAN_UPDATED not in types
    assert _stages(events) == [
        Stage.PLANNING,
        Stage.RESEARCHING,
        Stage.CHECKING,
        Stage.WRITING,
    ]


async def test_workflow_tool_failure_still_completes() -> None:
    """工具失败：一个任务挂了，会话仍出报告，缺口进数据限制。"""
    outcome, events = await _workflow(
        "Hyperliquid 怎么样？",
        runner=StubRunner(failures={"t1": RuntimeError("上游 429")}),
    )
    assert outcome.succeeded
    assert outcome.state.task_status["t1"] is TaskStatus.FAILED
    assert outcome.state.task_status["t2"] is TaskStatus.COMPLETED
    assert outcome.report is not None
    types = [event.type for event in events]
    assert EventType.AGENT_FAILED in types
    assert types[-1] is EventType.SESSION_COMPLETED
    assert "数据限制" in (events[-1].message or "")
    limitations = next(
        section for section in outcome.report.sections if section.id == "Data Limitations"
    )
    assert limitations.markdown.strip()


async def test_workflow_timeout_salvage_still_reports() -> None:
    """超时：salvage 数字仍进对比表，会话完成。"""
    report = _report_json(
        citation="",
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
    outcome, events = await _workflow(
        "比较 NVDA、AMD、AVGO",
        planner=_planner(_compare_plan()),
        runner=StubRunner(
            delays={"t3": 5.0},
            findings={
                "t1": _revenue("t1", "NVDA", 46_743_000_000.0),
                "t2": _revenue("t2", "AMD", 7_400_000_000.0),
            },
            salvages={"t3": _revenue("t3", "AVGO", 15_952_000_000.0)},
        ),
        writer=_writer(report),
        limits=_limits(task_timeout_s=0.05),
    )
    assert outcome.succeeded
    assert outcome.state.task_status["t3"] is TaskStatus.FAILED
    assert outcome.report is not None
    markdown = outcome.report.sections[0].markdown
    assert "| 指标 | NVDA | AMD | AVGO |" in markdown
    assert "15,952,000,000" in markdown
    assert events[-1].type is EventType.SESSION_COMPLETED


async def test_workflow_conflict_is_detected_before_writing() -> None:
    """冲突：多源数值不一致，撰写前发出 CONFLICT_DETECTED。"""
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
    finding = ResearchFinding(
        task_id="t1",
        agent=AgentName.CRYPTO_RESEARCH,
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
    outcome, events = await _workflow(
        "查询 HYPE 的 TVL",
        runner=StubRunner(findings={"t1": finding}),
    )
    assert outcome.succeeded
    assert len(outcome.state.conflicts) == 1
    types = [event.type for event in events]
    detected = [event for event in events if event.type is EventType.CONFLICT_DETECTED]
    assert len(detected) == 1
    conflict = _payload(detected[0], ConflictDetectedPayload).conflict
    assert conflict.values == ["coingecko: 1.2e+09 USD", "defillama: 1.8e+09 USD"]
    writing = next(
        i
        for i, event in enumerate(events)
        if event.type is EventType.STAGE_CHANGED
        and _payload(event, StageChangedPayload).stage is Stage.WRITING
    )
    assert types.index(EventType.CONFLICT_DETECTED) < writing


async def test_workflow_missing_citation_is_retried() -> None:
    """引用缺失：第一次 [99] 被检出，回喂后改成 [1]，不降级。"""
    source = _source()
    outcome, events = await _workflow(
        "Hyperliquid 怎么样？",
        runner=StubRunner(findings={"t1": _backed_finding(source)}),
        checker=_checker(_check_json()),
        writer=_writer(_report_json(citation="[99]"), _report_json(citation="[1]")),
    )
    assert outcome.succeeded
    assert outcome.report is not None
    assert "[1]" in outcome.report.executive_summary
    assert "[99]" not in outcome.report.executive_summary
    assert "[99]" not in outcome.report.sections[0].markdown
    warnings = [event for event in events if isinstance(event, WarningEvent)]
    assert CITATION_WARNING_CODE not in [event.payload.code for event in warnings]
    assert events[-1].type is EventType.SESSION_COMPLETED


async def test_workflow_schema_parse_failure_retries_then_fails_writer() -> None:
    """schema 解析失败：规划首次坏 JSON 能重试成功；Writer 三次都坏则整次失败。"""
    recovered, events = await _workflow(
        "Hyperliquid 怎么样？",
        planner=_planner(
            "这不是 JSON",
            _plan_json(),
            per_call=Usage(input_tokens=100, output_tokens=20),
        ),
    )
    assert recovered.succeeded
    metrics = [
        event
        for event in events
        if event.type is EventType.AGENT_RUN_METRICS
        and getattr(event.payload, "agent", None) is AgentName.RESEARCH_MANAGER
    ]
    assert len(metrics) == 1
    assert metrics[0].payload.usage == TokenUsage(input=200, output=40)

    failed, failed_events = await _workflow(
        "Hyperliquid 怎么样？",
        writer=_writer("这不是 JSON", "还不是", "仍然不是"),
    )
    assert not failed.succeeded
    assert failed.report is None
    assert failed_events[-1].type is EventType.SESSION_FAILED
    payload = _payload(failed_events[-1], SessionFailedPayload)
    assert payload.stage is Stage.WRITING
    assert payload.error.code == "structured_output"
