"""报告撰写阶段（§7.1 步骤 8，P2-8 / P2-9）。

Fact Checker 仍是 Phase 5；这里直接把 findings 交给 Report Writer。
第一次拿不到合法 JSON：没有报告就等于没有交付物，会话失败（§7.2）。
引用完整性不过：已有第一份报告，回喂改一次；再不过则降级标注，不让整次研究失败。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agent_service.agents.report_writer import (
    merge_report_gaps,
    report_writer_user_message,
)
from agent_service.models.structured_output import StructuredOutputError, run_structured
from agent_service.orchestrator.comparison import (
    build_comparison_table,
    ensure_comparison_table,
)
from agent_service.orchestrator.state import AgentRun
from agent_service.schemas.common import AgentName
from agent_service.schemas.events import (
    ErrorInfo,
    ReportCompletedEvent,
    ReportCompletedPayload,
    ReportStartedEvent,
    TokenUsage,
    WarningEvent,
    WarningPayload,
)
from agent_service.sources.citations import (
    assign_citation_indices,
    attach_section_claims,
    bibliography,
)
from agent_service.sources.guardrail import (
    CITATION_WARNING_CODE,
    apply_degradation,
    check_report,
    format_citation_feedback,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agent_service.agents.report_writer import ReportWriterAgent
    from agent_service.models.structured_output import StructuredRunResult
    from agent_service.orchestrator.state import ResearchState
    from agent_service.schemas.claims import Claim
    from agent_service.schemas.findings import ResearchFinding
    from agent_service.schemas.report import ResearchReport
    from agent_service.schemas.sources import Source
    from agent_service.sources.guardrail import CitationIssue


async def write_report(
    state: ResearchState,
    writer: ReportWriterAgent,
    *,
    now: datetime | None = None,
) -> ResearchReport:
    """编号 → 撰写 → 校验 → 一次修正 / 降级 → 发出 REPORT_* 事件。"""
    claims = [claim for finding in state.findings for claim in finding.claims]
    numbered = assign_citation_indices(state.source_registry.sources(), claims)
    state.source_registry.replace_all(numbered)
    _refresh_finding_sources(state, numbered)

    cited = bibliography(numbered)
    section_ids = tuple(state.plan.plan.report_sections) if state.plan is not None else ()
    moment = now or datetime.now(UTC)
    table = build_comparison_table(state.findings)
    user_input = report_writer_user_message(
        state.question,
        state.findings,
        numbered,
        section_ids=section_ids,
        conflicts=state.conflicts,
        comparison_table=table,
        now=moment,
    )

    state.bus.emit(ReportStartedEvent, payload=None, message="正在撰写研究报告")
    started = time.monotonic()

    try:
        structured = await run_structured(writer.agent, user_input, strategy=writer.strategy)
    except StructuredOutputError as error:
        _record(state, writer, started, usage=error.usage, error=error, at=moment)
        raise
    except Exception as error:
        _record(
            state,
            writer,
            started,
            usage=TokenUsage(),
            error=ErrorInfo(code=type(error).__name__, message=str(error)[:200]),
            at=moment,
        )
        raise

    report = ensure_comparison_table(merge_report_gaps(structured.output, state.findings), table)
    report, usage, leftover = await _revise(
        writer,
        structured,
        report,
        numbered=numbered,
        claims=claims,
        findings=state.findings,
        comparison_table=table,
    )
    if leftover:
        report = apply_degradation(report, numbered, leftover)
        _warn_citations(state, leftover)

    report = attach_section_claims(report, numbered, claims)
    state.report = report
    _record(state, writer, started, usage=usage, at=moment)
    state.bus.emit(
        ReportCompletedEvent,
        payload=ReportCompletedPayload(
            report=report,
            sources=cited,
            claims=list(claims),
            citation_count=len(cited),
        ),
        message="报告已生成",
    )
    return report


async def _revise(
    writer: ReportWriterAgent,
    structured: StructuredRunResult[ResearchReport],
    report: ResearchReport,
    *,
    numbered: Sequence[Source],
    claims: Sequence[Claim],
    findings: Sequence[ResearchFinding],
    comparison_table: str | None,
) -> tuple[ResearchReport, TokenUsage, list[CitationIssue]]:
    """第一次检查不过就回喂改一次。第二次仍不过：把问题交给调用方降级。"""
    issues = check_report(report, numbered, claims)
    usage = structured.usage
    if not issues:
        return report, usage, []

    follow_up = [
        *structured.result.to_input_list(),
        {"role": "user", "content": format_citation_feedback(issues)},
    ]
    try:
        retry = await run_structured(writer.agent, follow_up, strategy=writer.strategy)
    except StructuredOutputError as error:
        return report, usage + error.usage, issues

    usage = usage + retry.usage
    candidate = ensure_comparison_table(
        merge_report_gaps(retry.output, list(findings)), comparison_table
    )
    retry_issues = check_report(candidate, numbered, claims)
    if retry_issues:
        return candidate, usage, retry_issues
    return candidate, usage, []


def _warn_citations(state: ResearchState, issues: Sequence[CitationIssue]) -> None:
    summary = "；".join(item.message for item in issues)
    state.bus.emit(
        WarningEvent,
        payload=WarningPayload(code=CITATION_WARNING_CODE, message=summary),
        message="报告引用校验未完全通过，已降级标注",
    )


def _record(
    state: ResearchState,
    writer: ReportWriterAgent,
    started: float,
    *,
    usage: TokenUsage,
    error: ErrorInfo | StructuredOutputError | None = None,
    at: datetime,
) -> None:
    info: ErrorInfo | None
    if isinstance(error, StructuredOutputError):
        info = ErrorInfo(code="structured_output", message=str(error)[:200])
    else:
        info = error
    state.record_run(
        AgentRun(
            agent=AgentName.REPORT_WRITER,
            model=writer.entry,
            duration_ms=int((time.monotonic() - started) * 1000),
            token_usage_override=usage,
            prompt=writer.prompt,
            error=info,
        ),
        at=at,
    )


def _refresh_finding_sources(state: ResearchState, numbered: list[Source]) -> None:
    by_id = {source.id: source for source in numbered}
    updated: list[ResearchFinding] = []
    for finding in state.findings:
        updated.append(
            finding.model_copy(
                update={"sources": [by_id.get(source.id, source) for source in finding.sources]}
            )
        )
    state.findings = updated
