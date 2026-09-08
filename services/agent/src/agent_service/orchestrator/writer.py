"""报告撰写阶段（§7.1 步骤 8，P2-8）。

Fact Checker 仍是 Phase 5；这里直接把 findings 交给 Report Writer。
失败没有降级：没有报告就等于研究没有交付物（§7.2）。
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
from agent_service.orchestrator.state import AgentRun
from agent_service.schemas.common import AgentName
from agent_service.schemas.events import (
    ErrorInfo,
    ReportCompletedEvent,
    ReportCompletedPayload,
    ReportStartedEvent,
    TokenUsage,
)
from agent_service.sources.citations import assign_citation_indices, bibliography

if TYPE_CHECKING:
    from agent_service.agents.report_writer import ReportWriterAgent
    from agent_service.orchestrator.state import ResearchState
    from agent_service.schemas.findings import ResearchFinding
    from agent_service.schemas.report import ResearchReport
    from agent_service.schemas.sources import Source


async def write_report(
    state: ResearchState,
    writer: ReportWriterAgent,
    *,
    now: datetime | None = None,
) -> ResearchReport:
    """编号 → 撰写 → 合并缺口 → 发出 REPORT_* 事件。"""
    claims = [claim for finding in state.findings for claim in finding.claims]
    numbered = assign_citation_indices(state.source_registry.sources(), claims)
    state.source_registry.replace_all(numbered)
    _refresh_finding_sources(state, numbered)

    cited = bibliography(numbered)
    section_ids = tuple(state.plan.plan.report_sections) if state.plan is not None else ()
    moment = now or datetime.now(UTC)
    user_input = report_writer_user_message(
        state.question,
        state.findings,
        numbered,
        section_ids=section_ids,
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

    report = merge_report_gaps(structured.output, state.findings)
    state.report = report
    _record(state, writer, started, usage=structured.usage, at=moment)
    state.bus.emit(
        ReportCompletedEvent,
        payload=ReportCompletedPayload(
            report=report,
            sources=cited,
            citation_count=len(cited),
        ),
        message="报告已生成",
    )
    return report


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
