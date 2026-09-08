"""把 LLM 的 AgentFinding 和 tool 登记的来源拼成 ResearchFinding。

LLM 只输出短引用 s1/s2；URL 与 source_id 由代码补全（§15.1）。
"""

from __future__ import annotations

from agent_service.schemas.claims import Claim, ClaimDraft
from agent_service.schemas.common import ConfidenceLevel, EpistemicType
from agent_service.schemas.entities import MetricPoint
from agent_service.schemas.events import MetricFoundEvent, MetricFoundPayload
from agent_service.schemas.findings import AgentFinding, ResearchFinding
from agent_service.schemas.plan import ResearchTask
from agent_service.schemas.sources import Source
from agent_service.tools.web.collector import SourceCollector

EMPTY_WEB_SOURCES_GAP = "本次任务未获得任何网页来源"
EMPTY_CRYPTO_SOURCES_GAP = "本次任务未获得任何来源"
TIMEOUT_INCOMPLETE_GAP = (
    "任务超过 {timeout_s:.0f}s 未完成，未能输出结构化发现。以下来源与工具缺口是超时前已收集的。"
)
_SALVAGE_EXCERPT_CAP = 160


def assemble_finding(
    task: ResearchTask,
    draft: AgentFinding,
    collector: SourceCollector,
    *,
    empty_sources_gap: str = EMPTY_WEB_SOURCES_GAP,
) -> ResearchFinding:
    """把 LLM 草稿和 tool 登记的来源拼成 ResearchFinding。"""
    sources = collector.sources()
    by_ref = {source.ref: source for source in sources}
    claims: list[Claim] = []
    gaps = list(draft.data_gaps)
    unknown_refs: list[str] = []

    for item in draft.claims:
        resolved_ids: list[str] = []
        for ref in item.source_refs:
            source = by_ref.get(ref)
            if source is None:
                unknown_refs.append(ref)
                continue
            resolved_ids.append(source.id)
        epistemic = item.epistemic_type
        if epistemic is EpistemicType.SOURCE_BACKED_FACT and not resolved_ids:
            epistemic = EpistemicType.FACT
            gaps.append(f"陈述缺少可解析来源，已降级为 fact：{item.text}")
        claims.append(
            Claim(
                text=item.text,
                epistemic_type=epistemic,
                confidence=item.confidence,
                source_ids=resolved_ids,
                as_of=item.as_of,
                task_id=task.id,
                agent=task.agent.value,
            )
        )

    if unknown_refs:
        unique = ", ".join(dict.fromkeys(unknown_refs))
        gaps.append(f"claim 引用了工具结果中不存在的来源：{unique}")
    if not sources:
        gaps.append(empty_sources_gap)

    for error in collector.errors:
        if error.message and error.message not in gaps:
            gaps.append(error.message)

    metrics = [_metric(point, by_ref, gaps) for point in draft.metrics]
    bus = collector.registry.bus
    if bus is not None:
        for metric in metrics:
            bus.emit(MetricFoundEvent, payload=MetricFoundPayload(metric=metric))

    return ResearchFinding(
        task_id=task.id,
        agent=task.agent,
        summary=draft.summary,
        claims=claims,
        sources=sources,
        metrics=metrics,
        data_gaps=gaps,
        tool_errors=list(collector.errors),
    )


def salvage_finding(
    task: ResearchTask,
    collector: SourceCollector,
    *,
    timeout_s: float,
    empty_sources_gap: str = EMPTY_WEB_SOURCES_GAP,
) -> ResearchFinding:
    """超时取消时，把已登记的来源和工具缺口留给 Writer。

    没有 LLM 草稿就没有数字 claims；来源仍做成可引用陈述，否则
    `assign_citation_indices` 不会给孤儿来源发 [n]。
    """
    gap = TIMEOUT_INCOMPLETE_GAP.format(timeout_s=timeout_s)
    draft = AgentFinding(
        summary=gap,
        claims=_salvage_claims(collector.sources()),
        data_gaps=[gap],
    )
    return assemble_finding(task, draft, collector, empty_sources_gap=empty_sources_gap)


def _salvage_claims(sources: list[Source]) -> list[ClaimDraft]:
    return [
        ClaimDraft(
            text=_salvage_claim_text(source),
            epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
            confidence=ConfidenceLevel.MEDIUM,
            source_refs=[source.ref],
        )
        for source in sources
        if source.ref
    ]


def _salvage_claim_text(source: Source) -> str:
    title = (source.title or source.domain or source.url).strip()
    excerpt = " ".join((source.excerpt or "").split())
    if excerpt:
        if len(excerpt) > _SALVAGE_EXCERPT_CAP:
            excerpt = excerpt[:_SALVAGE_EXCERPT_CAP].rstrip() + "…"
        return f"{title}：{excerpt}"
    provider = source.provider or "未知来源"
    return f"已从 {provider} 登记来源：{title}"


def _metric(point: MetricPoint, by_ref: dict[str, Source], gaps: list[str]) -> MetricPoint:
    ref = point.source_ref
    if ref is None or ref in by_ref:
        return point
    gaps.append(f"指标 {point.name} 引用了不存在的来源：{ref}")
    return point.model_copy(update={"source_ref": None})
