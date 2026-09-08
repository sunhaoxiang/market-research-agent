"""多标的对比表（P4-11）。

数字对齐是代码的事，不交给 Writer 心算。按 `entity_symbol` × 指标名铺 GFM
表格；缺格子写「—」，不要当成 0。时间序列同一指标只留最新 `as_of`。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime

from agent_service.schemas.entities import MetricPoint
from agent_service.schemas.findings import ResearchFinding
from agent_service.schemas.report import ReportSection, ResearchReport

COMPARISON_SECTION_ID = "Comparison"
_EMPTY = "—"
_MIN_COMPARABLE = 2


def build_comparison_table(findings: Sequence[ResearchFinding]) -> str | None:
    """至少两个标的、且至少一行有两个标的都有数字，才生成表格。"""
    entities, rows = _matrix(findings)
    if len(entities) < _MIN_COMPARABLE or not rows:
        return None
    header = ["指标", *entities]
    lines = [
        "| " + " | ".join(_cell(item) for item in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for label, values in rows:
        cells = [label, *[values.get(symbol, _EMPTY) for symbol in entities]]
        lines.append("| " + " | ".join(_cell(item) for item in cells) + " |")
    return "\n".join(lines)


def ensure_comparison_table(draft: ResearchReport, table: str | None) -> ResearchReport:
    """模型漏贴或改数字时，把代码生成的表补进 Comparison 章节。"""
    if not table:
        return draft
    sections = list(draft.sections)
    index = next(
        (i for i, section in enumerate(sections) if section.id.casefold() == "comparison"),
        None,
    )
    if index is None:
        sections.append(
            ReportSection(id=COMPARISON_SECTION_ID, title="对比", markdown=table, claim_ids=[])
        )
        return draft.model_copy(update={"sections": sections})
    current = sections[index]
    if table in current.markdown:
        return draft
    markdown = current.markdown.rstrip()
    merged = table if not markdown else f"{markdown}\n\n{table}"
    sections[index] = current.model_copy(update={"markdown": merged})
    return draft.model_copy(update={"sections": sections})


def format_metric_value(value: float) -> str:
    if not math.isfinite(value):
        return _EMPTY
    nearest = round(value)
    if abs(value - nearest) <= 1e-9 * max(1.0, abs(value)):
        return f"{nearest:,}"
    return f"{value:.6g}"


def _matrix(
    findings: Sequence[ResearchFinding],
) -> tuple[list[str], list[tuple[str, dict[str, str]]]]:
    entities: list[str] = []
    cells: dict[tuple[str, str], dict[str, MetricPoint]] = {}
    labels: dict[tuple[str, str], str] = {}
    order: list[tuple[str, str]] = []

    for finding in findings:
        for metric in finding.metrics:
            symbol = (metric.entity_symbol or "").strip()
            if not symbol or not math.isfinite(metric.value):
                continue
            if symbol not in entities:
                entities.append(symbol)
            key = (metric.name.casefold(), (metric.unit or "").casefold())
            if key not in cells:
                cells[key] = {}
                labels[key] = _row_label(metric)
                order.append(key)
            current = cells[key].get(symbol)
            if current is None or _newer(metric, current):
                cells[key][symbol] = metric

    rows: list[tuple[str, dict[str, str]]] = []
    for key in order:
        by_entity = cells[key]
        if len(by_entity) < _MIN_COMPARABLE:
            continue
        values = {symbol: format_metric_value(point.value) for symbol, point in by_entity.items()}
        rows.append((labels[key], values))
    return entities, rows


def _row_label(metric: MetricPoint) -> str:
    label = (metric.label or metric.name).strip() or metric.name
    unit = (metric.unit or "").strip()
    if unit:
        return f"{label}（{unit}）"
    return label


def _newer(left: MetricPoint, right: MetricPoint) -> bool:
    return _as_of(left) >= _as_of(right)


def _as_of(metric: MetricPoint) -> datetime:
    if metric.as_of is None:
        return datetime.min.replace(tzinfo=UTC)
    if metric.as_of.tzinfo is None:
        return metric.as_of.replace(tzinfo=UTC)
    return metric.as_of.astimezone(UTC)


def _cell(text: str) -> str:
    return text.replace("|", "\\|")
