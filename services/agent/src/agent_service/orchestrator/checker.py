"""事实核查阶段（§7.1 步骤 6，P5-5 / D6 / D23）。

Merge 之后、撰写之前。只核验 FACT / SOURCE_BACKED_FACT（high-impact），
按优先级截断后再分批调用。失败发 WARNING 并继续写报告——Fact Checker
没有降级形态以外的失败权（§7.2：只有 Planner / Writer 失败才整次研究失败）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from agent_service.agents.fact_checker import fact_checker_user_message
from agent_service.agents.runtime import run_tool_agent
from agent_service.models.structured_output import StructuredOutputError
from agent_service.observability.sdk_events import AgentRunTranslator
from agent_service.orchestrator.state import AgentRun
from agent_service.schemas.common import (
    AgentName,
    ConfidenceLevel,
    EpistemicType,
    VerificationStatus,
)
from agent_service.schemas.events import (
    ClaimVerifiedEvent,
    ClaimVerifiedPayload,
    ConflictDetectedEvent,
    ConflictDetectedPayload,
    ErrorInfo,
    FactCheckProgressEvent,
    FactCheckProgressPayload,
    FactCheckStartedEvent,
    FactCheckStartedPayload,
    TokenUsage,
    WarningEvent,
    WarningPayload,
)
from agent_service.schemas.findings import ClaimVerification, FactCheckResult, ResearchFinding
from agent_service.tools.deps import ToolDeps
from agent_service.tools.web.collector import SourceCollector

if TYPE_CHECKING:
    from agent_service.agents.fact_checker import FactCheckerAgent
    from agent_service.config import ExecutionLimits
    from agent_service.orchestrator.state import ResearchState
    from agent_service.providers.fetch import PageFetcher
    from agent_service.providers.runtime import Clock
    from agent_service.providers.search import SearchProvider
    from agent_service.schemas.claims import Claim
    from agent_service.schemas.sources import Source

log = structlog.get_logger(__name__)

_HIGH_IMPACT = frozenset({EpistemicType.FACT, EpistemicType.SOURCE_BACKED_FACT})
_MAX_TOOL_CALLS = 6
"""复核检索的硬上限。低于 `max_tool_calls_per_agent`：D6 要控成本。"""

MAX_CHECK_CLAIMS = 12
"""单次研究最多核多少条。HYPE 深研 62 条一次塞进 180s，0 条裁定（D23）。"""

CHECK_BATCH_SIZE = 8
"""每批条数。一批超时不应带走已经核完的批次。"""

BATCH_TIMEOUT_S = 60.0
"""单批墙钟上限。总 `task_timeout_s` 仍是 180，但一批不该把整段预算吃光。"""

_MIN_BATCH_REMAINING_S = 20.0
"""剩余总预算少于此值则不再开下一批，把时间留给 Writer。"""

_CONFIDENCE_RANK = {
    ConfidenceLevel.HIGH: 0,
    ConfidenceLevel.MEDIUM: 1,
    ConfidenceLevel.LOW: 2,
}

FAILED_WARNING_CODE = "fact_check.failed"
TIMEOUT_WARNING_CODE = "fact_check.timeout"
SKIPPED_WARNING_CODE = "fact_check.skipped"
TRUNCATED_WARNING_CODE = "fact_check.truncated"


def high_impact_claims(findings: Sequence[ResearchFinding]) -> list[Claim]:
    """D6：只核验事实类陈述，跳过分析 / 推测 / 预测 / 观点。"""
    return [
        claim
        for finding in findings
        for claim in finding.claims
        if claim.epistemic_type in _HIGH_IMPACT
    ]


def select_claims_to_check(
    findings: Sequence[ResearchFinding],
    *,
    limit: int = MAX_CHECK_CLAIMS,
) -> list[Claim]:
    """D6 过滤后再按优先级取前 `limit` 条。同优先级保持 findings 原顺序。"""
    eligible = high_impact_claims(findings)
    ranked = sorted(
        enumerate(eligible),
        key=lambda item: (*_claim_priority(item[1]), item[0]),
    )
    return [claim for _, claim in ranked[:limit]]


async def check_facts(
    state: ResearchState,
    checker: FactCheckerAgent,
    *,
    limits: ExecutionLimits,
    search: SearchProvider | None = None,
    fetcher: PageFetcher | None = None,
    clock: Clock | None = None,
    now: datetime | None = None,
) -> None:
    """选出 high-impact claims，分批跑 Fact Checker，把裁定写回 findings。

    不抛业务异常：超时、结构化失败、工具异常都变成 WARNING，会话继续撰写。
    已成功批次的裁定会保留——一批超时不再等于整次核查没发生。
    """
    eligible = high_impact_claims(state.findings)
    claims = select_claims_to_check(state.findings)
    total = len(claims)
    state.bus.emit(
        FactCheckStartedEvent,
        payload=FactCheckStartedPayload(claim_count=total),
        message=_started_message(total),
    )
    dropped = len(eligible) - total
    if dropped > 0:
        _warn(
            state,
            TRUNCATED_WARNING_CODE,
            f"关键陈述 {len(eligible)} 条，按优先级核查前 {total} 条",
        )
    if total == 0:
        state.fact_check = FactCheckResult()
        return

    remaining = state.remaining_s(limits.total_timeout_s)
    if remaining <= 0:
        _warn(
            state,
            SKIPPED_WARNING_CODE,
            "整体时间预算已用尽，跳过事实核查",
        )
        return

    moment = now or (clock.now() if clock is not None else datetime.now(UTC))
    collector = SourceCollector(registry=state.source_registry)
    deps = ToolDeps(
        search=search,
        fetcher=fetcher,
        clock=clock,
        bus=state.bus,
        sources=collector,
    )
    translator = AgentRunTranslator(state.bus, agent=AgentName.FACT_CHECKER, task_id=None)
    catalog = state.source_registry.sources()
    session_ids = {claim.id for claim in claims}
    max_turns = min(limits.max_tool_calls_per_agent, _MAX_TOOL_CALLS) + 1

    for batch in _batches(claims, CHECK_BATCH_SIZE):
        remaining = state.remaining_s(limits.total_timeout_s)
        if remaining < _MIN_BATCH_REMAINING_S:
            _warn(
                state,
                SKIPPED_WARNING_CODE,
                "剩余时间不足，未核完的陈述保持未核验",
            )
            break
        timeout_s = min(limits.task_timeout_s, remaining, BATCH_TIMEOUT_S)
        user_input = fact_checker_user_message(
            batch,
            _sources_for_claims(batch, catalog),
            now=moment,
        )
        started = time.monotonic()
        try:
            async with asyncio.timeout(timeout_s):
                structured = await run_tool_agent(
                    checker.agent,
                    user_input,
                    strategy=checker.strategy,
                    deps=deps,
                    translator=translator,
                    max_turns=max_turns,
                )
        except TimeoutError:
            _record(state, checker, started, usage=TokenUsage(), error=_timeout_error(timeout_s))
            _warn(
                state,
                TIMEOUT_WARNING_CODE,
                f"本批事实核查超过 {timeout_s:.0f}s 未完成，已完成的裁定保留，继续撰写",
            )
            break
        except StructuredOutputError as error:
            _record(state, checker, started, usage=error.usage, error=error)
            _warn(state, FAILED_WARNING_CODE, "本批事实核查输出无法解析，尝试其余陈述")
            continue
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.exception("fact_check.unexpected_failure", session_id=state.session_id)
            _record(
                state,
                checker,
                started,
                usage=TokenUsage(),
                error=ErrorInfo(code=type(error).__name__, message=str(error)[:200]),
            )
            _warn(state, FAILED_WARNING_CODE, "事实核查失败，已完成的裁定保留，继续撰写")
            break
        else:
            _record(state, checker, started, usage=structured.usage)
            apply_fact_check(
                state,
                structured.output,
                allowed={claim.id for claim in batch},
                progress_ids=session_ids,
                progress_total=total,
            )
            catalog = state.source_registry.sources()


def apply_fact_check(
    state: ResearchState,
    result: FactCheckResult,
    *,
    allowed: set[str] | None = None,
    progress_ids: set[str] | None = None,
    progress_total: int | None = None,
) -> None:
    """把裁定写回 claims，发出 CLAIM_VERIFIED / 冲突事件。

    多次调用会合并 `verifications` / `conflicts` / `notes`，不覆盖先核完的批次。
    """
    if state.fact_check is None:
        state.fact_check = result
    else:
        prior = state.fact_check
        state.fact_check = FactCheckResult(
            verifications=[*prior.verifications, *result.verifications],
            conflicts=[*prior.conflicts, *result.conflicts],
            notes=[*prior.notes, *result.notes],
        )
    catalog = {item.ref: item for item in state.source_registry.sources()}
    index = _claim_index(state.findings)
    checkable = allowed if allowed is not None else set(index)
    scope = progress_ids if progress_ids is not None else checkable
    total = progress_total if progress_total is not None else len(scope)

    for verification in result.verifications:
        if verification.claim_id not in checkable:
            continue
        location = index.get(verification.claim_id)
        if location is None:
            continue
        finding_i, claim_i = location
        finding = state.findings[finding_i]
        claim = finding.claims[claim_i]
        extra = _resolve_refs(verification, catalog, already=set(claim.source_ids))
        updated = claim.model_copy(
            update={
                "verification": verification.verification,
                "verification_note": verification.note,
                "confidence": verification.confidence_adjustment or claim.confidence,
                "source_ids": [*claim.source_ids, *[item.id for item in extra]],
            }
        )
        claims = list(finding.claims)
        claims[claim_i] = updated
        sources = list(finding.sources)
        seen = {item.id for item in sources}
        for source in extra:
            if source.id not in seen:
                sources.append(source)
                seen.add(source.id)
        state.findings[finding_i] = finding.model_copy(
            update={"claims": claims, "sources": sources}
        )
        state.bus.emit(
            ClaimVerifiedEvent,
            payload=ClaimVerifiedPayload(
                claim_id=updated.id,
                verification=updated.verification,
                note=updated.verification_note,
            ),
            message=_verified_message(updated.verification.value),
        )
        state.bus.emit(
            FactCheckProgressEvent,
            payload=FactCheckProgressPayload(
                checked=_checked_count(state.findings, scope),
                total=total,
            ),
        )

    for conflict in result.conflicts:
        state.conflicts.append(conflict)
        state.bus.emit(
            ConflictDetectedEvent,
            payload=ConflictDetectedPayload(conflict=conflict),
            message=conflict.description,
        )


def _claim_priority(claim: Claim) -> tuple[int, int, int]:
    type_rank = 0 if claim.epistemic_type is EpistemicType.FACT else 1
    confidence_rank = _CONFIDENCE_RANK.get(claim.confidence, 3)
    sourced = 0 if claim.source_ids else 1
    return (type_rank, confidence_rank, sourced)


def _batches(items: Sequence[Claim], size: int) -> list[list[Claim]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def _sources_for_claims(claims: Sequence[Claim], sources: Sequence[Source]) -> list[Source]:
    wanted = {source_id for claim in claims for source_id in claim.source_ids}
    return [source for source in sources if source.id in wanted]


def _checked_count(findings: Sequence[ResearchFinding], scope: set[str]) -> int:
    return sum(
        1
        for finding in findings
        for claim in finding.claims
        if claim.id in scope and claim.verification is not VerificationStatus.UNVERIFIED
    )


def _claim_index(findings: Sequence[ResearchFinding]) -> dict[str, tuple[int, int]]:
    index: dict[str, tuple[int, int]] = {}
    for finding_i, finding in enumerate(findings):
        for claim_i, claim in enumerate(finding.claims):
            index[claim.id] = (finding_i, claim_i)
    return index


def _resolve_refs(
    verification: ClaimVerification,
    catalog: dict[str, Source],
    *,
    already: set[str],
) -> list[Source]:
    extra: list[Source] = []
    seen = set(already)
    for ref in verification.additional_source_refs:
        source = catalog.get(ref)
        if source is None or source.id in seen:
            continue
        extra.append(source)
        seen.add(source.id)
    return extra


def _started_message(total: int) -> str:
    if total == 0:
        return "没有需要核查的关键陈述"
    return f"开始核查 {total} 条关键陈述"


def _verified_message(status: str) -> str:
    labels = {
        "verified": "属实",
        "refuted": "证伪",
        "unsupported": "证据不足",
        "conflicting": "来源冲突",
        "unverified": "未核验",
    }
    return f"陈述裁定为{labels.get(status, status)}"


def _timeout_error(timeout_s: float) -> ErrorInfo:
    return ErrorInfo(code="task_timeout", message=f"本批事实核查超过 {timeout_s:.0f}s 未完成")


def _warn(state: ResearchState, code: str, message: str) -> None:
    state.bus.emit(
        WarningEvent,
        payload=WarningPayload(code=code, message=message),
        message=message,
    )


def _record(
    state: ResearchState,
    checker: FactCheckerAgent,
    started: float,
    *,
    usage: TokenUsage,
    error: ErrorInfo | StructuredOutputError | None = None,
) -> None:
    info: ErrorInfo | None
    if isinstance(error, StructuredOutputError):
        info = ErrorInfo(code="structured_output", message=str(error)[:200])
    else:
        info = error
    state.record_run(
        AgentRun(
            agent=AgentName.FACT_CHECKER,
            model=checker.entry,
            duration_ms=int((time.monotonic() - started) * 1000),
            token_usage_override=usage,
            prompt=checker.prompt,
            error=info,
        )
    )
