"""P7-8：跨模型 eval 对比表。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.compare import (
    CompareError,
    compare_reports,
    load_run_report,
    polarity_for,
    render_compare_markdown,
    resolve_run_path,
    run_compare,
    write_comparison,
)
from evals.paths import DATASETS_DIR, FIXTURES_DIR, REPO_ROOT, RESULTS_DIR
from evals.runner import EvalConfig, main, run_eval
from evals.schemas import (
    CaseResult,
    CaseStatus,
    EvalCompareReport,
    EvalRunReport,
    GitSnapshot,
    MetricName,
    MetricScore,
    RunSummary,
    SuiteResult,
)


def _report(**kwargs: object) -> EvalRunReport:
    started = datetime(2026, 9, 11, tzinfo=UTC)
    body: dict[str, object] = {
        "run_id": "run-a",
        "started_at": started,
        "finished_at": started,
        "duration_ms": 0,
        "git": GitSnapshot(sha="abc", dirty=False),
        "mode": "fixture",
        "label": "a",
        "model_ids": {"balanced": "deepseek:deepseek-flash"},
        "suites_requested": ["smoke"],
        "suites": [],
        "metrics": [
            MetricScore(name="hallucination_rate", value=0.02, n=10),
            MetricScore(name="citation_coverage", value=0.97, n=10),
        ],
        "summary": RunSummary(
            cases_total=10,
            passed=10,
            failed=0,
            errored=0,
            skipped=0,
            pass_rate=1.0,
        ),
    }
    body.update(kwargs)
    return EvalRunReport.model_validate(body)


def test_polarity_hallucination_is_lower_better() -> None:
    assert polarity_for(MetricName.HALLUCINATION_RATE.value) == "lower"
    assert polarity_for(MetricName.CITATION_COVERAGE.value) == "higher"


def test_core_metrics_pick_lower_hallucination() -> None:
    better = _report(run_id="flash", label="deepseek-flash")
    worse = _report(
        run_id="glm",
        label="glm-5.3",
        model_ids={"balanced": "zhipu:glm-5.3"},
        metrics=[
            MetricScore(name="hallucination_rate", value=0.09, n=10),
            MetricScore(name="citation_coverage", value=0.91, n=10),
        ],
        summary=RunSummary(
            cases_total=10,
            passed=9,
            failed=1,
            errored=0,
            skipped=0,
            pass_rate=0.9,
        ),
    )
    report = compare_reports([better, worse])
    assert report.winner_run_ids == ["flash"]
    assert "deepseek-flash" in report.summary
    markdown = render_compare_markdown(report)
    assert "综合更好" in markdown
    assert "hallucination_rate" in markdown
    assert "**0.0200**" in markdown


def test_identical_scores_are_tied() -> None:
    left = _report(run_id="a", label="model-a")
    right = _report(run_id="b", label="model-b", model_ids={"balanced": "zhipu:glm-5.3"})
    report = compare_reports([left, right])
    assert set(report.winner_run_ids) == {"a", "b"}
    assert "并列最优" in report.summary
    assert report.runs[0].rank == 1
    assert report.runs[1].rank == 1


def test_duplicate_run_id_is_rejected() -> None:
    with pytest.raises(CompareError, match="run_id"):
        compare_reports([_report(), _report()])


def test_single_report_is_rejected() -> None:
    with pytest.raises(CompareError, match="至少"):
        compare_reports([_report()])


def test_latency_p50_from_cases_when_missing() -> None:
    cases = [
        CaseResult(
            id="c1",
            suite="smoke",
            question="q",
            status=CaseStatus.PASS,
            scores={"smoke_pass_rate": 1.0},
            expected={},
            duration_ms=1000,
            grader="smoke",
        ),
        CaseResult(
            id="c2",
            suite="smoke",
            question="q",
            status=CaseStatus.PASS,
            scores={"smoke_pass_rate": 1.0},
            expected={},
            duration_ms=5000,
            grader="smoke",
        ),
        CaseResult(
            id="c3",
            suite="smoke",
            question="q",
            status=CaseStatus.PASS,
            scores={"smoke_pass_rate": 1.0},
            expected={},
            duration_ms=9000,
            grader="smoke",
        ),
    ]
    suite = SuiteResult(
        name="smoke",
        grader="smoke",
        cases=cases,
        metrics=[],
        passed=3,
        failed=0,
        errored=0,
        skipped=0,
    )
    fast_suite = suite.model_copy(
        update={"cases": [case.model_copy(update={"duration_ms": 200}) for case in cases]}
    )
    report = compare_reports(
        [
            _report(run_id="slow", label="slow", suites=[suite], metrics=[]),
            _report(run_id="fast", label="fast", suites=[fast_suite], metrics=[]),
        ]
    )
    latency = next(row for row in report.metrics if row.name == "latency_p50_s")
    assert latency.values["slow"] == 5.0
    assert latency.winner_run_ids == ["fast"]


def test_fixture_reports_declare_deepseek_better(tmp_path: Path) -> None:
    folder = FIXTURES_DIR / "compare"
    report, run_dir = run_compare(
        [folder / "deepseek-flash.json", folder / "glm-5.3.json"],
        out_dir=tmp_path,
        label="p7-8-fixture-compare",
        git=GitSnapshot(sha="p7-8-fixture", dirty=False),
        created_at=datetime(2026, 9, 11, tzinfo=UTC),
    )
    assert report.winner_run_ids == ["20260911T000000Z_deepseek-flash"]
    assert (run_dir / "comparison.json").is_file()
    markdown = (run_dir / "comparison.md").read_text(encoding="utf-8")
    assert "`deepseek-flash` 综合更好" in markdown
    assert "低更好" in markdown


def test_resolve_fixture_stem() -> None:
    path = resolve_run_path("deepseek-flash.json")
    loaded = load_run_report(path)
    assert loaded.label == "deepseek-flash"


def test_archived_fixture_comparison_picks_deepseek() -> None:
    payload = json.loads(
        (RESULTS_DIR / "p7-8-fixture-compare" / "comparison.json").read_text(encoding="utf-8")
    )
    report = EvalCompareReport.model_validate(payload)
    assert report.winner_run_ids == ["20260911T000000Z_deepseek-flash"]
    assert report.kind == "compare"


def test_cli_compare_writes_table(tmp_path: Path) -> None:
    folder = FIXTURES_DIR / "compare"
    code = main(
        [
            "--compare",
            f"{folder / 'deepseek-flash.json'},{folder / 'glm-5.3.json'}",
            "--out",
            str(tmp_path),
            "--label",
            "cli-compare",
        ]
    )
    assert code == 0
    runs = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert len(runs) == 1
    assert (runs[0] / "comparison.json").is_file()
    assert "综合更好" in (runs[0] / "comparison.md").read_text(encoding="utf-8")


def test_cli_compare_needs_two_reports() -> None:
    assert main(["--compare", "only-one.json"]) == 2


def test_load_rejects_compare_document(tmp_path: Path) -> None:
    better = _report(run_id="flash", label="flash")
    worse = _report(
        run_id="glm",
        label="glm",
        metrics=[
            MetricScore(name="hallucination_rate", value=0.2, n=1),
            MetricScore(name="citation_coverage", value=0.5, n=1),
        ],
    )
    comparison = compare_reports([better, worse])
    write_comparison(comparison, tmp_path)
    with pytest.raises(CompareError, match="对比报告"):
        load_run_report(tmp_path / "comparison.json")


@pytest.mark.asyncio
async def test_runner_records_role_model_ids(tmp_path: Path) -> None:
    report, _run_dir = await run_eval(
        EvalConfig(
            datasets_dir=DATASETS_DIR,
            results_dir=tmp_path,
            suites=("smoke",),
            repo_root=REPO_ROOT,
        )
    )
    assert set(report.model_ids) >= {"planner", "balanced", "fast", "writing"}
    assert all(report.model_ids.values())
