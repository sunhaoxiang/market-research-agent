"""多标的对比表（P4-11）。数字由代码对齐，缺格子是 — 不是 0。"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_service.orchestrator.comparison import (
    build_comparison_table,
    ensure_comparison_table,
    format_metric_value,
)
from agent_service.schemas.common import AgentName
from agent_service.schemas.entities import MetricPoint
from agent_service.schemas.findings import ResearchFinding
from agent_service.schemas.report import ReportSection, ResearchReport

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _metric(
    name: str,
    value: float,
    symbol: str,
    *,
    label: str | None = None,
    unit: str | None = "USD",
    as_of: datetime | None = None,
) -> MetricPoint:
    return MetricPoint(
        name=name,
        label=label or name,
        value=value,
        unit=unit,
        entity_symbol=symbol,
        as_of=as_of,
    )


def _finding(task_id: str, metrics: list[MetricPoint]) -> ResearchFinding:
    return ResearchFinding(
        task_id=task_id,
        agent=AgentName.STOCK_RESEARCH,
        summary=f"{task_id} 基本面",
        metrics=metrics,
    )


def test_format_large_integer_uses_thousands_separators() -> None:
    assert format_metric_value(46_743_000_000.0) == "46,743,000,000"
    assert format_metric_value(45.2) == "45.2"


def test_table_compares_overlapping_metrics_across_tickers() -> None:
    table = build_comparison_table(
        [
            _finding("t1", [_metric("revenue", 46_743_000_000.0, "NVDA", label="营收")]),
            _finding("t2", [_metric("revenue", 7_400_000_000.0, "AMD", label="营收")]),
            _finding("t3", [_metric("revenue", 15_000_000_000.0, "AVGO", label="营收")]),
        ]
    )
    assert table is not None
    assert table.splitlines()[0] == "| 指标 | NVDA | AMD | AVGO |"
    assert "营收（USD）" in table
    assert "46,743,000,000" in table
    assert "7,400,000,000" in table
    assert "15,000,000,000" in table


def test_salvage_metrics_fill_the_missing_ticker_column() -> None:
    """超时任务没有 LLM 草稿时，collector 抽出的数字仍要能长出第三列（D22）。"""
    salvaged = ResearchFinding(
        task_id="t3",
        agent=AgentName.STOCK_RESEARCH,
        summary="任务超过 180s 未完成",
        metrics=[_metric("revenue", 15_952_000_000.0, "AVGO", label="营收")],
        data_gaps=["任务超过 180s 未完成，未能输出结构化发现。"],
    )
    table = build_comparison_table(
        [
            _finding("t1", [_metric("revenue", 46_743_000_000.0, "NVDA", label="营收")]),
            _finding("t2", [_metric("revenue", 7_400_000_000.0, "AMD", label="营收")]),
            salvaged,
        ]
    )
    assert table is not None
    assert table.splitlines()[0] == "| 指标 | NVDA | AMD | AVGO |"
    assert "15,952,000,000" in table


def test_missing_cell_is_dash_not_zero() -> None:
    table = build_comparison_table(
        [
            _finding("t1", [_metric("revenue", 46_743_000_000.0, "NVDA", label="营收")]),
            _finding("t2", [_metric("revenue", 7_400_000_000.0, "AMD", label="营收")]),
            _finding("t3", [_metric("pe", 38.0, "AVGO", label="PE", unit="x")]),
        ]
    )
    assert table is not None
    revenue_row = next(line for line in table.splitlines() if "营收" in line)
    assert revenue_row.endswith("| — |") or "| — |" in revenue_row
    assert "0" not in revenue_row.split("|")[-2]
    assert "PE（x）" not in table


def test_single_ticker_does_not_make_a_table() -> None:
    assert (
        build_comparison_table(
            [_finding("t1", [_metric("revenue", 1.0, "NVDA"), _metric("pe", 2.0, "NVDA")])]
        )
        is None
    )


def test_series_keeps_latest_as_of() -> None:
    earlier = _NOW.replace(day=1)
    table = build_comparison_table(
        [
            _finding(
                "t1",
                [
                    _metric("price", 100.0, "NVDA", as_of=earlier),
                    _metric("price", 120.5, "NVDA", as_of=_NOW),
                ],
            ),
            _finding("t2", [_metric("price", 160.0, "AMD", as_of=_NOW)]),
        ]
    )
    assert table is not None
    assert "120.5" in table
    assert "100" not in table


def test_ensure_appends_table_when_model_omits_it() -> None:
    table = build_comparison_table(
        [
            _finding("t1", [_metric("pe", 45.2, "NVDA", unit="x")]),
            _finding("t2", [_metric("pe", 40.0, "AMD", unit="x")]),
        ]
    )
    assert table is not None
    draft = ResearchReport(
        title="对比",
        executive_summary="摘要",
        sections=[
            ReportSection(id="Comparison", title="对比", markdown="文字对照。", claim_ids=[])
        ],
    )
    merged = ensure_comparison_table(draft, table)
    assert "文字对照。" in merged.sections[0].markdown
    assert table in merged.sections[0].markdown
    again = ensure_comparison_table(merged, table)
    assert again.sections[0].markdown.count(table) == 1


def test_ensure_adds_comparison_section_if_missing() -> None:
    table = "| 指标 | NVDA | AMD |\n| --- | --- | --- |\n| PE（x） | 45.2 | 40 |"
    draft = ResearchReport(title="对比", executive_summary="摘要")
    merged = ensure_comparison_table(draft, table)
    assert merged.sections[0].id == "Comparison"
    assert merged.sections[0].markdown == table
