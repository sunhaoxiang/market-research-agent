"""Report Writer（P2-8 / P2-9）：产出带 [n] 引用的 Markdown，坏引用能被检出并修正。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from agents import Usage
from agents.testing import ScriptedModel, assistant_message
from pydantic import SecretStr

from agent_service.agents.report_writer import (
    ReportWriterAgent,
    build_report_writer,
    merge_report_gaps,
    report_writer_user_message,
)
from agent_service.models.registry import ModelRegistry
from agent_service.models.structured_output import StructuredOutputError, run_structured
from agent_service.observability.event_bus import EventBus
from agent_service.orchestrator.state import ResearchState
from agent_service.orchestrator.writer import write_report
from agent_service.schemas.claims import Claim
from agent_service.schemas.common import AgentName, ConfidenceLevel, EpistemicType, SourceType
from agent_service.schemas.events import (
    AgentRunMetricsEvent,
    EventType,
    ReportCompletedPayload,
    ResearchEvent,
    TokenUsage,
    WarningEvent,
)
from agent_service.schemas.findings import ResearchFinding
from agent_service.schemas.report import ResearchReport
from agent_service.schemas.sources import Source
from agent_service.sources.citations import assign_citation_indices
from agent_service.sources.guardrail import CITATION_WARNING_CODE
from agent_service.testing import IsolatedProviderCredentials, IsolatedSettings

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_URL = "https://theblock.co/hyperliquid-fee-share"


def _registry() -> ModelRegistry:
    return ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
        )
    )


def _source() -> Source:
    return Source(
        ref="s1",
        url=_URL,
        url_canonical=_URL,
        title="Fee share",
        domain="theblock.co",
        source_type=SourceType.NEWS,
        retrieved_at=_NOW,
    )


def _finding(source: Source) -> ResearchFinding:
    return ResearchFinding(
        task_id="t1",
        agent=AgentName.WEB_RESEARCH,
        summary="近期有手续费分享讨论。",
        claims=[
            Claim(
                text="Hyperliquid 正在讨论将部分交易手续费分享给 HYPE 持有人。",
                epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
                confidence=ConfidenceLevel.MEDIUM,
                source_ids=[source.id],
            )
        ],
        sources=[source],
        data_gaps=["未找到官方解锁时间表"],
    )


def test_user_message_uses_citation_numbers_not_urls() -> None:
    raw = _source()
    numbered = assign_citation_indices([raw], _finding(raw).claims)
    text = report_writer_user_message(
        "HYPE 最近有什么新闻",
        [_finding(numbered[0])],
        numbered,
        section_ids=("Overview", "Risks"),
        now=_NOW,
    )
    assert "[1] Fee share" in text
    assert "source_backed_fact" in text
    assert "2026-09-08" in text
    built = build_report_writer(_registry())
    assert "2026-09-08" not in str(built.agent.instructions)


def test_report_writer_prompt_hash_is_stable() -> None:
    first = build_report_writer(_registry())
    second = build_report_writer(_registry())
    assert first.prompt.hash == second.prompt.hash
    assert first.prompt.hash != ""


def test_merge_report_gaps_keeps_task_gaps() -> None:
    draft = ResearchReport(title="T", executive_summary="s", data_gaps=["模型提到的缺口"])
    merged = merge_report_gaps(draft, [_finding(_source())])
    assert "模型提到的缺口" in merged.data_gaps
    assert "未找到官方解锁时间表" in merged.data_gaps


async def test_scripted_writer_emits_markdown_with_citation() -> None:
    """验收：产出带引用的 Markdown。"""
    source = _source()
    numbered = assign_citation_indices([source], _finding(source).claims)
    finding = _finding(numbered[0])
    built = build_report_writer(_registry())
    payload = {
        "title": "HYPE 近况",
        "executive_summary": "Hyperliquid 正在讨论手续费分享。[1]",
        "sections": [
            {
                "id": "Overview",
                "title": "概述",
                "markdown": "Hyperliquid 正在讨论把部分交易手续费分享给 HYPE 持有人。[1]",
                "claim_ids": [finding.claims[0].id],
            }
        ],
        "data_gaps": ["未找到官方解锁时间表"],
    }
    scripted = built.agent.clone(
        model=ScriptedModel([[assistant_message(json.dumps(payload, ensure_ascii=False))]])
    )
    structured = await run_structured(
        scripted,
        report_writer_user_message("HYPE 最近有什么新闻", [finding], numbered, now=_NOW),
        strategy=built.strategy,
    )
    report = merge_report_gaps(structured.output, [finding])
    assert "[1]" in report.executive_summary
    assert "[1]" in report.sections[0].markdown
    assert report.sections[0].id == "Overview"
    assert "未找到官方解锁时间表" in report.data_gaps


def _report_payload(finding: ResearchFinding, citation: str) -> str:
    return json.dumps(
        {
            "title": "HYPE 近况",
            "executive_summary": f"Hyperliquid 正在讨论手续费分享。{citation}",
            "sections": [
                {
                    "id": "Overview",
                    "title": "概述",
                    "markdown": (
                        f"Hyperliquid 正在讨论把部分交易手续费分享给 HYPE 持有人。{citation}"
                    ),
                    "claim_ids": [finding.claims[0].id],
                }
            ],
            "data_gaps": ["未找到官方解锁时间表"],
        },
        ensure_ascii=False,
    )


def _writer(*replies: str, per_call: Usage | None = None) -> ReportWriterAgent:
    built = build_report_writer(_registry())
    scripted = ScriptedModel(
        [[assistant_message(reply)] for reply in replies],
        default_usage=per_call,
    )
    return ReportWriterAgent(
        agent=built.agent.clone(model=scripted),
        strategy=built.strategy,
        entry=built.entry,
        prompt=built.prompt,
    )


async def _drain(bus: EventBus) -> list[ResearchEvent]:
    bus.close()
    return [event async for event in bus.stream()]


async def _write(
    *replies: str, per_call: Usage | None = None
) -> tuple[ResearchState, list[ResearchEvent]]:
    source = _source()
    finding = _finding(source)
    bus = EventBus("sess-w", heartbeat_interval_s=60.0)
    state = ResearchState("sess-w", "HYPE 最近有什么新闻", bus=bus)
    state.source_registry.replace_all([source])
    state.findings = [finding]
    await write_report(state, _writer(*replies, per_call=per_call), now=_NOW)
    return state, await _drain(bus)


async def test_write_report_passes_without_retry() -> None:
    """一次合法输出且引用完整：不要再消耗一轮模型调用。"""
    source = _source()
    finding = _finding(source)
    state, events = await _write(_report_payload(finding, "[1]"))
    assert state.report is not None
    assert "[1]" in state.report.executive_summary
    assert EventType.WARNING not in [event.type for event in events]
    metrics = [event for event in events if isinstance(event, AgentRunMetricsEvent)]
    assert len(metrics) == 1
    assert metrics[0].payload.error is None
    completed = next(event for event in events if event.type is EventType.REPORT_COMPLETED)
    assert isinstance(completed.payload, ReportCompletedPayload)
    assert completed.payload.claims[0].text == state.findings[0].claims[0].text


async def test_write_report_backfills_claim_ids() -> None:
    """章节徽标靠代码回填，不信模型抄的 claim id。"""
    invented = json.dumps(
        {
            "title": "HYPE 近况",
            "executive_summary": "Hyperliquid 正在讨论手续费分享。[1]",
            "sections": [
                {
                    "id": "Overview",
                    "title": "概述",
                    "markdown": "Hyperliquid 正在讨论把部分交易手续费分享给 HYPE 持有人。[1]",
                    "claim_ids": ["llm-invented-id"],
                }
            ],
            "data_gaps": ["未找到官方解锁时间表"],
        },
        ensure_ascii=False,
    )
    state, events = await _write(invented)
    claim_id = state.findings[0].claims[0].id
    assert state.report is not None
    assert state.report.sections[0].claim_ids == [claim_id]
    completed = next(event for event in events if event.type is EventType.REPORT_COMPLETED)
    assert isinstance(completed.payload, ReportCompletedPayload)
    assert completed.payload.report.sections[0].claim_ids == [claim_id]


async def test_write_report_retries_on_bad_citation() -> None:
    """验收：故意注入坏引用能被检出并修正。"""
    source = _source()
    finding = _finding(source)
    state, events = await _write(
        _report_payload(finding, "[99]"),
        _report_payload(finding, "[1]"),
        per_call=Usage(input_tokens=100, output_tokens=20),
    )
    assert state.report is not None
    assert "[1]" in state.report.executive_summary
    assert "[99]" not in state.report.executive_summary
    assert EventType.WARNING not in [event.type for event in events]
    types = [event.type for event in events]
    assert types.index(EventType.REPORT_STARTED) < types.index(EventType.REPORT_COMPLETED)
    metrics = [event for event in events if isinstance(event, AgentRunMetricsEvent)]
    assert len(metrics) == 1
    assert metrics[0].payload.usage == TokenUsage(input=200, output=40)


async def test_uncorrected_citation_is_stripped_with_warning() -> None:
    """两轮都坏：剥掉无法解析的 [n]，发 warning，仍然交付报告。"""
    source = _source()
    finding = _finding(source)
    bad = _report_payload(finding, "[99]")
    state, events = await _write(bad, bad)
    assert state.report is not None
    assert "[99]" not in state.report.executive_summary
    assert "[99]" not in state.report.sections[0].markdown
    assert any("引用完整性校验未完全通过" in gap for gap in state.report.data_gaps)
    warnings = [event for event in events if isinstance(event, WarningEvent)]
    assert len(warnings) == 1
    assert warnings[0].payload.code == CITATION_WARNING_CODE
    assert EventType.REPORT_COMPLETED in [event.type for event in events]
    metrics = [event for event in events if isinstance(event, AgentRunMetricsEvent)]
    assert len(metrics) == 1
    assert metrics[0].payload.error is None


async def test_first_json_failure_still_fails_the_session() -> None:
    """根本没有报告：按 P2-8，会话失败。"""
    bus = EventBus("sess-w", heartbeat_interval_s=60.0)
    state = ResearchState("sess-w", "HYPE 最近有什么新闻", bus=bus)
    state.source_registry.replace_all([_source()])
    state.findings = [_finding(_source())]
    with pytest.raises(StructuredOutputError):
        await write_report(
            state,
            _writer("这不是 JSON", "还不是", "仍然不是"),
            now=_NOW,
        )
    events = await _drain(bus)
    assert EventType.REPORT_STARTED in [event.type for event in events]
    assert EventType.REPORT_COMPLETED not in [event.type for event in events]
    metrics = [event for event in events if isinstance(event, AgentRunMetricsEvent)]
    assert len(metrics) == 1
    assert metrics[0].payload.error is not None
    assert metrics[0].payload.error.code == "structured_output"
