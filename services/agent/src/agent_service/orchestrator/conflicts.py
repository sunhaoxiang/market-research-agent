"""Merge 阶段的数值冲突检测（§7.1 步骤 5 / §23 R9，P3-10）。

多源对同一指标给出不同数字时，发出 `CONFLICT_DETECTED`，报告并列展示，
**不取平均**。判定是纯代码：不把「这两个数算不算冲突」交给 LLM。
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC
from typing import TYPE_CHECKING

from agent_service.schemas.events import ConflictDetectedEvent, ConflictDetectedPayload
from agent_service.schemas.findings import Conflict

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agent_service.orchestrator.state import ResearchState
    from agent_service.schemas.entities import MetricPoint
    from agent_service.schemas.findings import ResearchFinding
    from agent_service.schemas.sources import Source

RELATIVE_TOLERANCE = 0.01
"""相对差超过 1% 视为冲突。舍入噪声通常远小于此；TVL/市值算法差异通常远大于此。"""
_MIN_OBSERVATIONS = 2


def detect_metric_conflicts(
    findings: Sequence[ResearchFinding],
    sources: Sequence[Source] | None = None,
    *,
    relative_tolerance: float = RELATIVE_TOLERANCE,
) -> list[Conflict]:
    """按 (name, 标的, 单位, 数据日) 分组，组内最大相对差超过阈值则记一条冲突。"""
    by_ref = _source_index(findings, sources)
    groups: dict[tuple[str, str, str, str], list[MetricPoint]] = defaultdict(list)
    for finding in findings:
        for metric in finding.metrics:
            if not math.isfinite(metric.value):
                continue
            groups[_metric_key(metric)].append(metric)

    conflicts: list[Conflict] = []
    for members in groups.values():
        if len(members) < _MIN_OBSERVATIONS or not _spread_exceeds(members, relative_tolerance):
            continue
        conflicts.append(_conflict(members, findings, by_ref))
    return conflicts


def record_metric_conflicts(state: ResearchState) -> list[Conflict]:
    """检测、记入账本、逐条发出 `CONFLICT_DETECTED`。无冲突则不发事件。"""
    conflicts = detect_metric_conflicts(state.findings, state.source_registry.sources())
    state.conflicts = conflicts
    for conflict in conflicts:
        state.bus.emit(
            ConflictDetectedEvent,
            payload=ConflictDetectedPayload(conflict=conflict),
            message=conflict.description,
        )
    return conflicts


def _source_index(
    findings: Sequence[ResearchFinding],
    sources: Sequence[Source] | None,
) -> dict[str, Source]:
    catalog: list[Source] = list(sources or [])
    seen = {item.ref for item in catalog}
    for finding in findings:
        for source in finding.sources:
            if source.ref not in seen:
                catalog.append(source)
                seen.add(source.ref)
    return {item.ref: item for item in catalog}


def _metric_key(metric: MetricPoint) -> tuple[str, str, str, str]:
    as_of = ""
    if metric.as_of is not None:
        as_of = metric.as_of.astimezone(UTC).date().isoformat()
    return (
        metric.name.casefold(),
        (metric.entity_symbol or "").casefold(),
        (metric.unit or "").casefold(),
        as_of,
    )


def _spread_exceeds(metrics: Sequence[MetricPoint], relative_tolerance: float) -> bool:
    values = [item.value for item in metrics]
    lo, hi = min(values), max(values)
    scale = max(abs(lo), abs(hi))
    if scale == 0:
        return False
    return (hi - lo) / scale > relative_tolerance


def _conflict(
    metrics: Sequence[MetricPoint],
    findings: Sequence[ResearchFinding],
    by_ref: dict[str, Source],
) -> Conflict:
    sample = metrics[0]
    label = sample.label or sample.name
    scope = f"{label}（{sample.entity_symbol}）" if sample.entity_symbol else label
    values = [
        _observation(item, by_ref.get(item.source_ref) if item.source_ref else None)
        for item in metrics
    ]
    return Conflict(
        claim_ids=_claim_ids(metrics, findings, by_ref),
        description=f"{scope} 多源数值不一致，已并列保留各来源结果，未取平均。",
        values=values,
    )


def _observation(metric: MetricPoint, source: Source | None) -> str:
    who = "未知来源"
    if source is not None:
        who = source.provider or source.domain or source.ref
    number = f"{metric.value:g}"
    if metric.unit:
        number = f"{number} {metric.unit}"
    return f"{who}: {number}"


def _claim_ids(
    metrics: Sequence[MetricPoint],
    findings: Sequence[ResearchFinding],
    by_ref: dict[str, Source],
) -> list[str]:
    wanted: set[str] = set()
    for metric in metrics:
        ref = metric.source_ref
        if ref is not None and ref in by_ref:
            wanted.add(by_ref[ref].id)
    if not wanted:
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for finding in findings:
        for claim in finding.claims:
            if claim.id in seen or not wanted.intersection(claim.source_ids):
                continue
            seen.add(claim.id)
            ids.append(claim.id)
    return ids
