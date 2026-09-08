"""Merge & Dedup（§7.1 步骤 5，P5-4）。

执行结束后、撰写之前：按 `url_canonical` 把跨 Agent 的重复来源收成一条，
折叠字面重复的陈述，再跑数值冲突检测。全程纯代码，不让 LLM 判断
「这两条算不算同一条」。
"""

from __future__ import annotations

from datetime import UTC

from agent_service.orchestrator.conflicts import record_metric_conflicts
from agent_service.orchestrator.state import ResearchState
from agent_service.schemas.claims import Claim
from agent_service.schemas.common import ConfidenceLevel, EpistemicType, SourceType
from agent_service.schemas.entities import MetricPoint
from agent_service.schemas.findings import ResearchFinding
from agent_service.schemas.sources import Source
from agent_service.sources.canonical import canonicalize_url
from agent_service.sources.reliability import classify_reliability

_TYPE_RANK = {
    SourceType.WEB: 0,
    SourceType.SOCIAL: 1,
    SourceType.API: 2,
    SourceType.GITHUB: 3,
    SourceType.NEWS: 4,
    SourceType.DOCS: 5,
    SourceType.OFFICIAL: 6,
    SourceType.SEC: 7,
}
_CONFIDENCE_RANK = {
    ConfidenceLevel.LOW: 0,
    ConfidenceLevel.MEDIUM: 1,
    ConfidenceLevel.HIGH: 2,
}
_FACT_KINDS = frozenset({EpistemicType.FACT, EpistemicType.SOURCE_BACKED_FACT})


def merge_research(state: ResearchState) -> None:
    """来源归一 → 陈述合并 → 冲突汇总。结果写回 `state`。"""
    _merge_sources(state)
    _merge_claims(state)
    record_metric_conflicts(state)


def _merge_sources(state: ResearchState) -> None:
    survivors: dict[str, Source] = {}
    order: list[str] = []
    id_map: dict[str, str] = {}
    used_refs: set[str] = set()

    for source in (*state.source_registry.sources(), *_finding_sources(state.findings)):
        key = _canonical(source)
        existing = survivors.get(key)
        if existing is None:
            adopted = _adopt_ref(source, used_refs)
            survivors[key] = adopted
            order.append(key)
            id_map[source.id] = adopted.id
            continue
        id_map[source.id] = existing.id
        blended = _blend(existing, source)
        if blended is not existing:
            survivors[key] = blended

    catalog = [survivors[key] for key in order]
    state.source_registry.replace_all(catalog)
    by_id = {item.id: item for item in catalog}
    state.findings = [
        _rewrite_finding(finding, id_map=id_map, by_id=by_id) for finding in state.findings
    ]


def _merge_claims(state: ResearchState) -> None:
    kept: dict[tuple[str, str, str], Claim] = {}
    updated: list[ResearchFinding] = []
    for finding in state.findings:
        remaining: list[Claim] = []
        for claim in finding.claims:
            key = _claim_key(claim)
            prior = kept.get(key)
            if prior is None:
                kept[key] = claim
                remaining.append(claim)
                continue
            _union_claim(prior, claim)
        updated.append(finding.model_copy(update={"claims": remaining}))
    state.findings = updated


def _finding_sources(findings: list[ResearchFinding]) -> list[Source]:
    seen: set[str] = set()
    sources: list[Source] = []
    for finding in findings:
        for source in finding.sources:
            if source.id in seen:
                continue
            seen.add(source.id)
            sources.append(source)
    return sources


def _canonical(source: Source) -> str:
    if source.url_canonical.strip():
        return source.url_canonical
    return canonicalize_url(source.url)


def _adopt_ref(source: Source, used_refs: set[str]) -> Source:
    if source.ref not in used_refs:
        used_refs.add(source.ref)
        return source
    nxt = _next_ref(used_refs)
    used_refs.add(nxt)
    return source.model_copy(update={"ref": nxt})


def _next_ref(used_refs: set[str]) -> str:
    index = 1
    while f"s{index}" in used_refs:
        index += 1
    return f"s{index}"


def _blend(kept: Source, incoming: Source) -> Source:
    updates: dict[str, object] = {}
    if incoming.title and not kept.title:
        updates["title"] = incoming.title
    incoming_excerpt = incoming.excerpt
    if incoming_excerpt and (kept.excerpt is None or len(incoming_excerpt) > len(kept.excerpt)):
        updates["excerpt"] = incoming_excerpt
    if incoming.published_at is not None and kept.published_at is None:
        updates["published_at"] = incoming.published_at
    if incoming.http_status is not None and kept.http_status is None:
        updates["http_status"] = incoming.http_status
    if _TYPE_RANK[incoming.source_type] > _TYPE_RANK[kept.source_type]:
        updates["source_type"] = incoming.source_type
        updates["reliability"] = classify_reliability(
            kept.url_canonical,
            source_type=incoming.source_type,
            domain=kept.domain or incoming.domain,
        )
    if not updates:
        return kept
    return kept.model_copy(update=updates)


def _rewrite_finding(
    finding: ResearchFinding,
    *,
    id_map: dict[str, str],
    by_id: dict[str, Source],
) -> ResearchFinding:
    sources: list[Source] = []
    seen: set[str] = set()
    ref_map: dict[str, str] = {}
    for source in finding.sources:
        survivor_id = id_map.get(source.id, source.id)
        survivor = by_id.get(survivor_id, source)
        ref_map[source.ref] = survivor.ref
        if survivor_id in seen:
            continue
        seen.add(survivor_id)
        sources.append(survivor)
    claims = [_remap_claim(claim, id_map) for claim in finding.claims]
    metrics = [_remap_metric(metric, ref_map) for metric in finding.metrics]
    return finding.model_copy(update={"sources": sources, "claims": claims, "metrics": metrics})


def _remap_claim(claim: Claim, id_map: dict[str, str]) -> Claim:
    ids: list[str] = []
    seen: set[str] = set()
    for source_id in claim.source_ids:
        mapped = id_map.get(source_id, source_id)
        if mapped in seen:
            continue
        seen.add(mapped)
        ids.append(mapped)
    if ids == claim.source_ids:
        return claim
    return claim.model_copy(update={"source_ids": ids})


def _remap_metric(metric: MetricPoint, ref_map: dict[str, str]) -> MetricPoint:
    ref = metric.source_ref
    if ref is None or ref not in ref_map or ref_map[ref] == ref:
        return metric
    return metric.model_copy(update={"source_ref": ref_map[ref]})


def _claim_key(claim: Claim) -> tuple[str, str, str]:
    text = " ".join(claim.text.split()).casefold()
    kind = "fact" if claim.epistemic_type in _FACT_KINDS else claim.epistemic_type.value
    as_of = ""
    if claim.as_of is not None:
        moment = claim.as_of if claim.as_of.tzinfo is not None else claim.as_of.replace(tzinfo=UTC)
        as_of = moment.astimezone(UTC).date().isoformat()
    return (text, kind, as_of)


def _union_claim(kept: Claim, incoming: Claim) -> None:
    ids = list(dict.fromkeys([*kept.source_ids, *incoming.source_ids]))
    kept.source_ids = ids
    if _CONFIDENCE_RANK[incoming.confidence] > _CONFIDENCE_RANK[kept.confidence]:
        kept.confidence = incoming.confidence
    if kept.epistemic_type in _FACT_KINDS and (
        incoming.epistemic_type is EpistemicType.SOURCE_BACKED_FACT or ids
    ):
        kept.epistemic_type = EpistemicType.SOURCE_BACKED_FACT
    if incoming.as_of is not None and kept.as_of is None:
        kept.as_of = incoming.as_of
