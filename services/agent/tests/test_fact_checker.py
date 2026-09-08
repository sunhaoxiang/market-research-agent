"""Fact Checker（P5-5）：干净上下文、只核验 high-impact、能识别注入的错误声明。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from agents.testing import ScriptedModel, assistant_message
from pydantic import SecretStr

from agent_service.agents.fact_checker import (
    FactCheckerAgent,
    build_fact_checker,
    fact_checker_user_message,
)
from agent_service.agents.report_writer import ReportWriterAgent, build_report_writer
from agent_service.agents.research_manager import PlannerAgent, build_research_manager
from agent_service.models.registry import ModelRegistry
from agent_service.observability.event_bus import EventBus
from agent_service.orchestrator.checker import (
    FAILED_WARNING_CODE,
    apply_fact_check,
    high_impact_claims,
)
from agent_service.orchestrator.executor import TaskContext
from agent_service.orchestrator.pipeline import run_research
from agent_service.orchestrator.state import ResearchState
from agent_service.schemas.claims import Claim
from agent_service.schemas.common import (
    AgentName,
    ConfidenceLevel,
    EpistemicType,
    SourceType,
    Stage,
    VerificationStatus,
)
from agent_service.schemas.events import (
    ClaimVerifiedPayload,
    EventType,
    FactCheckStartedPayload,
    ResearchEvent,
    StageChangedPayload,
    WarningPayload,
)
from agent_service.schemas.findings import ClaimVerification, FactCheckResult, ResearchFinding
from agent_service.schemas.sources import Source
from agent_service.testing import (
    IsolatedExecutionLimits,
    IsolatedProviderCredentials,
    IsolatedSettings,
)

_NOW = datetime(2026, 9, 8, tzinfo=UTC)
_FALSE_CLAIM_ID = "claim-false-tvl"
_ANALYSIS_CLAIM_ID = "claim-analysis"
_URL = "https://defillama.com/protocol/hyperliquid"


def _registry() -> ModelRegistry:
    return ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
        )
    )


def _limits() -> IsolatedExecutionLimits:
    return IsolatedExecutionLimits()


def _source(*, ref: str = "s1") -> Source:
    return Source(
        ref=ref,
        url=_URL,
        url_canonical=_URL,
        title="Hyperliquid TVL",
        domain="defillama.com",
        source_type=SourceType.API,
        provider="defillama",
        retrieved_at=_NOW,
        excerpt="TVL is in the billions of dollars.",
    )


def _false_claim(source: Source) -> Claim:
    return Claim(
        id=_FALSE_CLAIM_ID,
        text="HYPE 的 TVL 为 1 美元。",
        epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
        confidence=ConfidenceLevel.HIGH,
        source_ids=[source.id],
        task_id="t1",
        agent=AgentName.CRYPTO_RESEARCH.value,
    )


def _analysis_claim() -> Claim:
    return Claim(
        id=_ANALYSIS_CLAIM_ID,
        text="HYPE 可能很快会有手续费分享。",
        epistemic_type=EpistemicType.ANALYSIS,
        confidence=ConfidenceLevel.MEDIUM,
        task_id="t1",
        agent=AgentName.CRYPTO_RESEARCH.value,
    )


def _plan_json() -> str:
    return json.dumps(
        {
            "question_type": "crypto",
            "interpretation": "用户想了解 HYPE 的 TVL",
            "entities": [],
            "tasks": [
                {
                    "id": "t1",
                    "agent": "crypto_research",
                    "objective": "查询 HYPE 的 TVL",
                    "entities": [],
                    "suggested_tools": [],
                    "depends_on": [],
                    "priority": 0,
                }
            ],
            "report_sections": ["Overview"],
            "assumptions": [],
        },
        ensure_ascii=False,
    )


def _report_json() -> str:
    return json.dumps(
        {
            "title": "HYPE TVL",
            "executive_summary": "注入的错误 TVL 陈述应被核查标出。",
            "sections": [
                {
                    "id": "Overview",
                    "title": "概述",
                    "markdown": "公开数据与「TVL 为 1 美元」不符。",
                    "claim_ids": [],
                }
            ],
            "data_gaps": [],
        },
        ensure_ascii=False,
    )


def _check_json(*, claim_id: str = _FALSE_CLAIM_ID, verification: str = "refuted") -> str:
    return json.dumps(
        {
            "verifications": [
                {
                    "claim_id": claim_id,
                    "verification": verification,
                    "note": "公开 TVL 远高于 1 美元，该陈述被证伪。",
                    "confidence_adjustment": "low",
                    "additional_source_refs": [],
                }
            ],
            "conflicts": [],
            "notes": [],
        },
        ensure_ascii=False,
    )


def _planner() -> PlannerAgent:
    built = build_research_manager(_registry(), _limits())
    return PlannerAgent(
        agent=built.agent.clone(model=ScriptedModel([[assistant_message(_plan_json())]])),
        strategy=built.strategy,
        entry=built.entry,
        prompt=built.prompt,
    )


def _writer() -> ReportWriterAgent:
    built = build_report_writer(_registry())
    return ReportWriterAgent(
        agent=built.agent.clone(model=ScriptedModel([[assistant_message(_report_json())]])),
        strategy=built.strategy,
        entry=built.entry,
        prompt=built.prompt,
    )


def _checker(*replies: str) -> FactCheckerAgent:
    built = build_fact_checker(_registry())
    scripted = ScriptedModel([[assistant_message(reply)] for reply in replies or (_check_json(),)])
    return FactCheckerAgent(
        agent=built.agent.clone(model=scripted),
        strategy=built.strategy,
        entry=built.entry,
        prompt=built.prompt,
    )


class _InjectedRunner:
    def __init__(self, finding: ResearchFinding) -> None:
        self._finding = finding

    def model_id_for(self, agent: AgentName) -> str:
        del agent
        return "deepseek:deepseek-v4-flash"

    async def run(self, context: TaskContext, state: ResearchState) -> ResearchFinding:
        del context, state
        return self._finding


def _bus() -> EventBus:
    return EventBus("sess-p5-5", heartbeat_interval_s=60.0)


async def _drain(bus: EventBus) -> list[ResearchEvent]:
    bus.close()
    return [event async for event in bus.stream()]


def _payload[T](event: ResearchEvent, payload_type: type[T]) -> T:
    payload = event.payload
    assert isinstance(payload, payload_type)
    return payload


def test_fact_checker_prompt_hash_is_stable() -> None:
    first = build_fact_checker(_registry())
    second = build_fact_checker(_registry())
    assert first.prompt.hash == second.prompt.hash
    assert first.prompt.hash != ""
    assert "web_search" in str(first.agent.instructions)
    assert "news_search" not in str(first.agent.instructions)


def test_user_message_is_clean_context() -> None:
    source = _source()
    claim = _false_claim(source)
    text = fact_checker_user_message([claim], [source], now=_NOW)
    assert "HYPE 的 TVL 为 1 美元。" in text
    assert claim.id in text
    assert source.ref in text
    assert "2026-09-08" in text
    assert "用户问题" not in text
    assert "Hyperliquid 怎么样" not in text
    built = build_fact_checker(_registry())
    assert "2026-09-08" not in str(built.agent.instructions)


def test_high_impact_skips_analysis() -> None:
    source = _source()
    finding = ResearchFinding(
        task_id="t1",
        agent=AgentName.CRYPTO_RESEARCH,
        summary="摘要不应进入核查上下文。",
        claims=[_false_claim(source), _analysis_claim()],
        sources=[source],
    )
    selected = high_impact_claims([finding])
    assert [claim.id for claim in selected] == [_FALSE_CLAIM_ID]


def test_apply_writes_verification_and_skips_analysis() -> None:
    source = _source()
    bus = _bus()
    state = ResearchState("sess-p5-5", "问题", bus=bus)
    state.findings = [
        ResearchFinding(
            task_id="t1",
            agent=AgentName.CRYPTO_RESEARCH,
            summary="摘要",
            claims=[_false_claim(source), _analysis_claim()],
            sources=[source],
        )
    ]
    apply_fact_check(
        state,
        FactCheckResult(
            verifications=[
                ClaimVerification(
                    claim_id=_FALSE_CLAIM_ID,
                    verification=VerificationStatus.REFUTED,
                    note="数量级不对",
                ),
                ClaimVerification(
                    claim_id=_ANALYSIS_CLAIM_ID,
                    verification=VerificationStatus.REFUTED,
                    note="不该核这条",
                ),
            ]
        ),
        allowed={_FALSE_CLAIM_ID},
    )
    by_id = {claim.id: claim for finding in state.findings for claim in finding.claims}
    assert by_id[_FALSE_CLAIM_ID].verification is VerificationStatus.REFUTED
    assert by_id[_FALSE_CLAIM_ID].verification_note == "数量级不对"
    assert by_id[_ANALYSIS_CLAIM_ID].verification is VerificationStatus.UNVERIFIED


async def test_injected_false_claim_is_refuted() -> None:
    """验收：能识别注入的错误声明。"""
    source = _source()
    finding = ResearchFinding(
        task_id="t1",
        agent=AgentName.CRYPTO_RESEARCH,
        summary="TVL 相关发现。",
        claims=[_false_claim(source), _analysis_claim()],
        sources=[source],
    )
    bus = _bus()
    outcome = await run_research(
        "查询 HYPE 的 TVL",
        planner=_planner(),
        runner=_InjectedRunner(finding),
        writer=_writer(),
        fact_checker=_checker(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    assert outcome.succeeded
    claims = [claim for item in outcome.findings for claim in item.claims]
    by_id = {claim.id: claim for claim in claims}
    assert by_id[_FALSE_CLAIM_ID].verification is VerificationStatus.REFUTED
    assert by_id[_ANALYSIS_CLAIM_ID].verification is VerificationStatus.UNVERIFIED
    assert outcome.report is not None

    events = await _drain(bus)
    types = [event.type for event in events]
    assert EventType.FACT_CHECK_STARTED in types
    assert EventType.CLAIM_VERIFIED in types
    started = next(event for event in events if event.type is EventType.FACT_CHECK_STARTED)
    assert _payload(started, FactCheckStartedPayload).claim_count == 1
    verified = next(event for event in events if event.type is EventType.CLAIM_VERIFIED)
    payload = _payload(verified, ClaimVerifiedPayload)
    assert payload.claim_id == _FALSE_CLAIM_ID
    assert payload.verification is VerificationStatus.REFUTED

    stages = [
        _payload(event, StageChangedPayload).stage
        for event in events
        if event.type is EventType.STAGE_CHANGED
    ]
    assert stages == [Stage.PLANNING, Stage.RESEARCHING, Stage.CHECKING, Stage.WRITING]
    assert types.index(EventType.FACT_CHECK_STARTED) < types.index(EventType.CLAIM_VERIFIED)
    writing = next(
        i
        for i, event in enumerate(events)
        if event.type is EventType.STAGE_CHANGED
        and _payload(event, StageChangedPayload).stage is Stage.WRITING
    )
    assert types.index(EventType.CLAIM_VERIFIED) < writing


async def test_no_high_impact_skips_the_model() -> None:
    finding = ResearchFinding(
        task_id="t1",
        agent=AgentName.CRYPTO_RESEARCH,
        summary="只有分析。",
        claims=[_analysis_claim()],
    )
    bus = _bus()
    checker = _checker("this must not be consumed")
    outcome = await run_research(
        "HYPE 怎么看",
        planner=_planner(),
        runner=_InjectedRunner(finding),
        writer=_writer(),
        fact_checker=checker,
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )
    assert outcome.succeeded
    events = await _drain(bus)
    started = [event for event in events if event.type is EventType.FACT_CHECK_STARTED]
    assert len(started) == 1
    assert _payload(started[0], FactCheckStartedPayload).claim_count == 0
    assert EventType.CLAIM_VERIFIED not in [event.type for event in events]
    assert not any(
        event.type is EventType.AGENT_RUN_METRICS
        and getattr(event.payload, "agent", None) is AgentName.FACT_CHECKER
        for event in events
    )


async def test_checker_failure_does_not_fail_the_session() -> None:
    source = _source()
    finding = ResearchFinding(
        task_id="t1",
        agent=AgentName.CRYPTO_RESEARCH,
        summary="TVL 相关发现。",
        claims=[_false_claim(source)],
        sources=[source],
    )
    bus = _bus()
    outcome = await run_research(
        "查询 HYPE 的 TVL",
        planner=_planner(),
        runner=_InjectedRunner(finding),
        writer=_writer(),
        fact_checker=_checker("not-json", "still-not-json"),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )
    assert outcome.succeeded
    claim = outcome.findings[0].claims[0]
    assert claim.verification is VerificationStatus.UNVERIFIED
    events = await _drain(bus)
    warnings = [event for event in events if event.type is EventType.WARNING]
    assert any(_payload(event, WarningPayload).code == FAILED_WARNING_CODE for event in warnings)
    assert EventType.SESSION_COMPLETED in [event.type for event in events]
    assert EventType.SESSION_FAILED not in [event.type for event in events]
