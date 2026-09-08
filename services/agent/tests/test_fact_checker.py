"""Fact Checker（P5-5）：干净上下文、只核验 high-impact、能识别注入的错误声明。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from agents.testing import ScriptedModel, assistant_message
from pydantic import SecretStr

from agent_service.agents.fact_checker import (
    FactCheckerAgent,
    build_fact_checker,
    fact_checker_user_message,
)
from agent_service.agents.report_writer import ReportWriterAgent, build_report_writer
from agent_service.agents.research_manager import PlannerAgent, build_research_manager
from agent_service.agents.runtime import ToolAgentRun
from agent_service.models.registry import ModelRegistry
from agent_service.observability.event_bus import EventBus
from agent_service.orchestrator.checker import (
    CHECK_BATCH_SIZE,
    FAILED_WARNING_CODE,
    MAX_CHECK_CLAIMS,
    SKIPPED_WARNING_CODE,
    TIMEOUT_WARNING_CODE,
    TRUNCATED_WARNING_CODE,
    apply_fact_check,
    check_facts,
    high_impact_claims,
    select_claims_to_check,
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
    FactCheckProgressPayload,
    FactCheckStartedPayload,
    ResearchEvent,
    StageChangedPayload,
    TokenUsage,
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


def _claim(
    claim_id: str,
    *,
    epistemic: EpistemicType = EpistemicType.SOURCE_BACKED_FACT,
    confidence: ConfidenceLevel = ConfidenceLevel.LOW,
    source_ids: list[str] | None = None,
) -> Claim:
    return Claim(
        id=claim_id,
        text=f"陈述 {claim_id}",
        epistemic_type=epistemic,
        confidence=confidence,
        source_ids=source_ids or [],
        task_id="t1",
        agent=AgentName.CRYPTO_RESEARCH.value,
    )


def _finding(claims: list[Claim], sources: list[Source] | None = None) -> ResearchFinding:
    return ResearchFinding(
        task_id="t1",
        agent=AgentName.CRYPTO_RESEARCH,
        summary="摘要",
        claims=claims,
        sources=sources or [],
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


def _check_json_many(claim_ids: list[str], *, verification: str = "verified") -> str:
    return json.dumps(
        {
            "verifications": [
                {
                    "claim_id": claim_id,
                    "verification": verification,
                    "note": "核过",
                    "confidence_adjustment": "high",
                    "additional_source_refs": [],
                }
                for claim_id in claim_ids
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


def test_apply_merges_batches_instead_of_replacing() -> None:
    first = _claim("claim-a", confidence=ConfidenceLevel.HIGH)
    second = _claim("claim-b", confidence=ConfidenceLevel.HIGH)
    bus = _bus()
    state = ResearchState("sess-p5-5", "问题", bus=bus)
    state.findings = [_finding([first, second])]
    apply_fact_check(
        state,
        FactCheckResult(
            verifications=[
                ClaimVerification(
                    claim_id="claim-a",
                    verification=VerificationStatus.VERIFIED,
                    note="第一批",
                )
            ],
            notes=["batch-1"],
        ),
        allowed={"claim-a"},
        progress_ids={"claim-a", "claim-b"},
        progress_total=2,
    )
    apply_fact_check(
        state,
        FactCheckResult(
            verifications=[
                ClaimVerification(
                    claim_id="claim-b",
                    verification=VerificationStatus.REFUTED,
                    note="第二批",
                )
            ],
            notes=["batch-2"],
        ),
        allowed={"claim-b"},
        progress_ids={"claim-a", "claim-b"},
        progress_total=2,
    )
    assert state.fact_check is not None
    assert [item.claim_id for item in state.fact_check.verifications] == ["claim-a", "claim-b"]
    assert state.fact_check.notes == ["batch-1", "batch-2"]
    by_id = {claim.id: claim for finding in state.findings for claim in finding.claims}
    assert by_id["claim-a"].verification is VerificationStatus.VERIFIED
    assert by_id["claim-b"].verification is VerificationStatus.REFUTED


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


def test_select_ranks_fact_high_over_low_source_backed() -> None:
    source = _source()
    lows = [_claim(f"claim-low-{i:02d}", source_ids=[source.id]) for i in range(12)]
    analysis = _analysis_claim()
    unsourced_high = _claim(
        "claim-sbf-high-unsourced",
        confidence=ConfidenceLevel.HIGH,
    )
    sourced_high = _claim(
        "claim-sbf-high-sourced",
        confidence=ConfidenceLevel.HIGH,
        source_ids=[source.id],
    )
    fact_high = _claim(
        "claim-fact-high",
        epistemic=EpistemicType.FACT,
        confidence=ConfidenceLevel.HIGH,
        source_ids=[source.id],
    )
    finding = _finding(
        [*lows, analysis, unsourced_high, sourced_high, fact_high],
        [source],
    )
    selected = select_claims_to_check([finding])
    assert len(selected) == MAX_CHECK_CLAIMS
    assert [claim.id for claim in selected[:3]] == [
        "claim-fact-high",
        "claim-sbf-high-sourced",
        "claim-sbf-high-unsourced",
    ]
    assert [claim.id for claim in selected[3:]] == [f"claim-low-{i:02d}" for i in range(9)]
    assert "claim-low-09" not in {claim.id for claim in selected}
    assert _ANALYSIS_CLAIM_ID not in {claim.id for claim in selected}


async def test_truncates_started_count_and_warns() -> None:
    source = _source()
    claims = [_claim(f"claim-{i:02d}", source_ids=[source.id]) for i in range(15)]
    bus = _bus()
    state = ResearchState("sess-d23", "问题", bus=bus)
    state.source_registry.replace_all([source])
    state.findings = [_finding(claims, [source])]
    selected = [claim.id for claim in select_claims_to_check(state.findings)]
    assert selected == [f"claim-{i:02d}" for i in range(MAX_CHECK_CLAIMS)]
    await check_facts(
        state,
        _checker(
            _check_json_many(selected[:CHECK_BATCH_SIZE]),
            _check_json_many(selected[CHECK_BATCH_SIZE:]),
        ),
        limits=_limits(),
        now=_NOW,
    )
    events = await _drain(bus)
    started = next(event for event in events if event.type is EventType.FACT_CHECK_STARTED)
    assert _payload(started, FactCheckStartedPayload).claim_count == MAX_CHECK_CLAIMS
    warnings = [
        _payload(event, WarningPayload) for event in events if event.type is EventType.WARNING
    ]
    truncated = next(item for item in warnings if item.code == TRUNCATED_WARNING_CODE)
    assert "15" in truncated.message
    assert "12" in truncated.message
    by_id = {claim.id: claim for finding in state.findings for claim in finding.claims}
    assert by_id["claim-00"].verification is VerificationStatus.VERIFIED
    assert by_id["claim-11"].verification is VerificationStatus.VERIFIED
    assert by_id["claim-12"].verification is VerificationStatus.UNVERIFIED


async def test_check_facts_splits_into_batches() -> None:
    source = _source()
    claims = [
        _claim(f"claim-{i:02d}", confidence=ConfidenceLevel.HIGH, source_ids=[source.id])
        for i in range(MAX_CHECK_CLAIMS)
    ]
    bus = _bus()
    state = ResearchState("sess-d23", "问题", bus=bus)
    state.source_registry.replace_all([source])
    state.findings = [_finding(claims, [source])]
    ids = [claim.id for claim in claims]
    await check_facts(
        state,
        _checker(
            _check_json_many(ids[:CHECK_BATCH_SIZE]),
            _check_json_many(ids[CHECK_BATCH_SIZE:]),
        ),
        limits=_limits(),
        now=_NOW,
    )
    by_id = {claim.id: claim for finding in state.findings for claim in finding.claims}
    assert all(by_id[claim_id].verification is VerificationStatus.VERIFIED for claim_id in ids)
    assert state.fact_check is not None
    assert len(state.fact_check.verifications) == MAX_CHECK_CLAIMS
    events = await _drain(bus)
    metrics = [
        event
        for event in events
        if event.type is EventType.AGENT_RUN_METRICS
        and getattr(event.payload, "agent", None) is AgentName.FACT_CHECKER
    ]
    assert len(metrics) == 2
    progress = [
        _payload(event, FactCheckProgressPayload)
        for event in events
        if event.type is EventType.FACT_CHECK_PROGRESS
    ]
    assert progress[-1].checked == MAX_CHECK_CLAIMS
    assert progress[-1].total == MAX_CHECK_CLAIMS


async def test_later_batch_timeout_keeps_first_and_skips_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    claims = [
        _claim(f"claim-{i:02d}", confidence=ConfidenceLevel.HIGH, source_ids=[source.id])
        for i in range(MAX_CHECK_CLAIMS)
    ]
    bus = _bus()
    state = ResearchState("sess-d23", "问题", bus=bus)
    state.source_registry.replace_all([source])
    state.findings = [_finding(claims, [source])]
    monkeypatch.setattr("agent_service.orchestrator.checker.CHECK_BATCH_SIZE", 4)
    calls = {"n": 0}

    async def fake_run(*args: object, **kwargs: object) -> ToolAgentRun[FactCheckResult]:
        del args, kwargs
        calls["n"] += 1
        if calls["n"] == 1:
            return _verified_run([claim.id for claim in claims[:4]])
        raise TimeoutError

    monkeypatch.setattr("agent_service.orchestrator.checker.run_tool_agent", fake_run)
    await check_facts(state, _checker(), limits=_limits(), now=_NOW)
    assert calls["n"] == 2
    by_id = {claim.id: claim for finding in state.findings for claim in finding.claims}
    assert by_id["claim-00"].verification is VerificationStatus.VERIFIED
    assert by_id["claim-03"].verification is VerificationStatus.VERIFIED
    assert by_id["claim-04"].verification is VerificationStatus.UNVERIFIED
    events = await _drain(bus)
    warnings = [
        _payload(event, WarningPayload) for event in events if event.type is EventType.WARNING
    ]
    assert any(item.code == TIMEOUT_WARNING_CODE for item in warnings)


async def test_skips_later_batch_when_budget_low(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source()
    claims = [
        _claim(f"claim-{i:02d}", confidence=ConfidenceLevel.HIGH, source_ids=[source.id])
        for i in range(MAX_CHECK_CLAIMS)
    ]
    bus = _bus()
    state = ResearchState("sess-d23", "问题", bus=bus)
    state.source_registry.replace_all([source])
    state.findings = [_finding(claims, [source])]
    remaining = [100.0, 100.0, 5.0]

    def fake_remaining(total_timeout_s: float) -> float:
        del total_timeout_s
        return remaining.pop(0) if remaining else 5.0

    monkeypatch.setattr(state, "remaining_s", fake_remaining)
    calls = {"n": 0}

    async def fake_run(*args: object, **kwargs: object) -> ToolAgentRun[FactCheckResult]:
        del args, kwargs
        calls["n"] += 1
        return _verified_run([claim.id for claim in claims[:CHECK_BATCH_SIZE]])

    monkeypatch.setattr("agent_service.orchestrator.checker.run_tool_agent", fake_run)
    await check_facts(state, _checker(), limits=_limits(), now=_NOW)
    assert calls["n"] == 1
    by_id = {claim.id: claim for finding in state.findings for claim in finding.claims}
    assert by_id["claim-00"].verification is VerificationStatus.VERIFIED
    assert by_id["claim-08"].verification is VerificationStatus.UNVERIFIED
    events = await _drain(bus)
    warnings = [
        _payload(event, WarningPayload) for event in events if event.type is EventType.WARNING
    ]
    assert any(item.code == SKIPPED_WARNING_CODE for item in warnings)


def _verified_run(claim_ids: list[str]) -> ToolAgentRun[FactCheckResult]:
    return ToolAgentRun(
        output=FactCheckResult(
            verifications=[
                ClaimVerification(
                    claim_id=claim_id,
                    verification=VerificationStatus.VERIFIED,
                    note="ok",
                )
                for claim_id in claim_ids
            ]
        ),
        attempts=1,
        usage=TokenUsage(),
    )
