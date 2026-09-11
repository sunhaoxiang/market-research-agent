"""P7-2 确定性 grader：同一输入分数不变。"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.datasets_io import load_jsonl
from evals.graders.deterministic import (
    DeterministicGrader,
    grade_citation_coverage,
    grade_citation_validity,
    grade_numeric,
    grade_report,
    grade_routing,
    grade_tools,
)
from evals.paths import DATASETS_DIR
from evals.producers import IntentRoutingProducer
from evals.runner import EvalConfig, run_eval
from evals.schemas import CaseStatus, EvalCase, Observation


def _case(suite: str, expected: dict, **kwargs: object) -> EvalCase:
    body: dict[str, object] = {
        "id": "c1",
        "suite": suite,
        "question": "q",
        "expected": expected,
    }
    body.update(kwargs)
    return EvalCase.model_validate(body)


def test_routing_set_compare_is_order_insensitive() -> None:
    expected = {
        "question_type": "compare",
        "entities": ["AVGO", "NVDA", "AMD"],
        "agents": ["stock_research"],
    }
    payload = {
        "question_type": "compare",
        "entities": ["NVDA", "AMD", "AVGO"],
        "agents": ["stock_research"],
    }
    part = grade_routing(expected, payload)
    assert part is not None
    assert part.scores["agent_routing_accuracy"] == 1.0


def test_routing_mismatch_is_zero() -> None:
    part = grade_routing(
        {"question_type": "stock", "agents": ["stock_research"]},
        {"question_type": "crypto", "agents": ["crypto_research"]},
    )
    assert part is not None
    assert part.scores["agent_routing_accuracy"] == 0.0


def test_tool_recall_and_precision_with_optional() -> None:
    part = grade_tools(
        {"tools": ["get_tvl", "get_market_data"], "optional_tools": ["web_search"]},
        {"tools": ["get_tvl", "get_market_data", "web_search"]},
    )
    assert part is not None
    assert part.scores["tool_selection_recall"] == 1.0
    assert part.scores["tool_selection_precision"] == 1.0


def test_tool_extra_hurts_precision() -> None:
    part = grade_tools(
        {"tools": ["get_tvl"]},
        {"tools": ["get_tvl", "web_fetch"]},
    )
    assert part is not None
    assert part.scores["tool_selection_recall"] == 1.0
    assert part.scores["tool_selection_precision"] == 0.5


def test_citation_coverage_counts_fact_claims() -> None:
    payload = {
        "claims": [
            {
                "id": "c1",
                "epistemic_type": "source_backed_fact",
                "source_ids": ["s1"],
            },
            {"id": "c2", "epistemic_type": "fact", "source_ids": []},
            {"id": "c3", "epistemic_type": "analysis", "source_ids": []},
        ]
    }
    part = grade_citation_coverage({}, payload)
    assert part is not None
    assert part.scores["citation_coverage"] == 0.5
    assert part.actual["uncited_fact_ids"] == ["c2"]


def test_citation_validity_requires_excerpt_in_page() -> None:
    payload = {
        "claims": [{"id": "c1", "epistemic_type": "source_backed_fact", "source_ids": ["s1"]}],
        "sources": [
            {
                "id": "s1",
                "url": "https://example.com/a",
                "excerpt": "TVL $1.8b",
                "http_status": 200,
                "page_text": "Hyperliquid TVL $1.8b across perps.",
            }
        ],
    }
    ok = grade_citation_validity({}, payload)
    assert ok is not None
    assert ok.scores["citation_validity"] == 1.0

    payload["sources"][0]["page_text"] = "unrelated page"
    bad = grade_citation_validity({}, payload)
    assert bad is not None
    assert bad.scores["citation_validity"] == 0.0


def test_citation_validity_rejects_non_http_and_error_status() -> None:
    payload = {
        "claims": [{"epistemic_type": "fact", "source_ids": ["s1", "s2"]}],
        "sources": [
            {"id": "s1", "url": "ftp://x", "excerpt": "hi", "page_text": "hi"},
            {
                "id": "s2",
                "url": "https://ok.example",
                "excerpt": "hi",
                "http_status": 404,
                "page_text": "hi",
            },
        ],
    }
    part = grade_citation_validity({}, payload)
    assert part is not None
    assert part.scores["citation_validity"] == 0.0


def test_report_completeness_rejects_placeholder() -> None:
    expected = {"report_sections": ["Overview", "Risks"]}
    payload = {
        "executive_summary": "结论。",
        "sections": [
            {"id": "Overview", "markdown": "有内容。"},
            {"id": "Risks", "markdown": "本节暂无足够材料，详见数据限制。"},
        ],
    }
    part = grade_report(expected, payload)
    assert part is not None
    assert part.scores["report_completeness"] == pytest.approx(2 / 3)
    assert "Risks" in part.actual["missing_sections"]


def test_numeric_tolerance_is_relative() -> None:
    expected = {"metrics": [{"name": "tvl_usd", "value": 100.0, "rel": 0.05}]}
    close = grade_numeric(expected, {"metrics": [{"name": "tvl_usd", "value": 104.0}]})
    far = grade_numeric(expected, {"metrics": [{"name": "tvl_usd", "value": 120.0}]})
    assert close is not None and close.scores["numeric_accuracy"] == 1.0
    assert far is not None and far.scores["numeric_accuracy"] == 0.0


def test_numeric_absolute_tolerance() -> None:
    expected = {"metrics": [{"name": "pe_ttm", "value": 45.2, "abs": 0.1}]}
    part = grade_numeric(expected, {"metrics": [{"name": "pe_ttm", "value": 45.25}]})
    assert part is not None
    assert part.scores["numeric_accuracy"] == 1.0


def test_grader_is_deterministic() -> None:
    grader = DeterministicGrader()
    case = _case(
        "tool_selection",
        {"tools": ["get_tvl", "get_market_data"]},
    )
    observation = Observation(payload={"tools": ["get_market_data", "get_tvl"]})
    first = grader.grade(case, observation)
    second = grader.grade(case, observation)
    assert first.scores == second.scores
    assert first.status is CaseStatus.PASS
    assert first.scores == {
        "tool_selection_recall": 1.0,
        "tool_selection_precision": 1.0,
    }


def test_grader_fails_when_any_metric_misses_target() -> None:
    grader = DeterministicGrader()
    case = _case("tool_selection", {"tools": ["get_tvl", "get_market_data"]})
    result = grader.grade(case, Observation(payload={"tools": ["get_tvl"]}))
    assert result.status is CaseStatus.FAIL
    assert result.scores["tool_selection_recall"] == 0.5


@pytest.mark.asyncio
async def test_intent_producer_uses_rules() -> None:
    producer = IntentRoutingProducer()
    observation = await producer.produce(
        _case(
            "intent_routing",
            {"question_type": "stock"},
            question="分析 NVDA 最近一季财报",
        )
    )
    assert observation.payload["question_type"] == "stock"
    assert observation.payload["entities"] == ["NVDA"]
    assert observation.payload["agents"] == ["stock_research"]
    assert observation.payload["source"] == "rules"


@pytest.mark.asyncio
async def test_seed_suites_all_pass(tmp_path: Path) -> None:
    report, _run_dir = await run_eval(
        EvalConfig(
            datasets_dir=DATASETS_DIR,
            results_dir=tmp_path,
            suites=(
                "intent_routing",
                "tool_selection",
                "crypto_project",
                "stock_analysis",
                "financial_report",
            ),
        )
    )
    assert report.summary.failed == 0
    assert report.summary.errored == 0
    names = {item.name for item in report.metrics}
    assert "agent_routing_accuracy" in names
    assert "tool_selection_recall" in names
    assert "citation_coverage" in names
    assert "citation_validity" in names
    assert "report_completeness" in names
    assert "numeric_accuracy" in names
    again, _ = await run_eval(
        EvalConfig(
            datasets_dir=DATASETS_DIR,
            results_dir=tmp_path / "second",
            suites=("intent_routing", "crypto_project"),
        )
    )
    first = {item.name: item.value for item in report.metrics if item.name != "smoke_pass_rate"}
    second = {item.name: item.value for item in again.metrics}
    for name, value in second.items():
        assert first[name] == value


def test_seed_intent_dataset_matches_filename() -> None:
    cases = load_jsonl(DATASETS_DIR / "intent_routing.jsonl")
    assert len(cases) >= 6
    assert all(case.suite == "intent_routing" for case in cases)
