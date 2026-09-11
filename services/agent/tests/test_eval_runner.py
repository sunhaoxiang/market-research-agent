"""P7-1 eval runner：可跑 smoke，写出 JSON + Markdown。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.archive import make_run_id, render_markdown, summarize_counts
from evals.datasets_io import DatasetError, load_jsonl
from evals.graders.smoke import SmokeGrader, SmokeProducer
from evals.paths import DATASETS_DIR, REPO_ROOT
from evals.registry import SUITE_HARNESS, SuiteHarness
from evals.runner import EvalConfig, main, run_eval
from evals.schemas import (
    CaseResult,
    CaseStatus,
    EvalCase,
    EvalRunReport,
    GitSnapshot,
    Observation,
    RunSummary,
    SuiteResult,
)


def test_smoke_dataset_loads() -> None:
    cases = load_jsonl(DATASETS_DIR / "smoke.jsonl")
    assert [case.id for case in cases] == [
        "smoke-echo-empty",
        "smoke-echo-ascii",
        "smoke-echo-zh",
    ]
    assert cases[2].question == "分析 NVDA 最近一季财报"


def test_load_jsonl_rejects_bad_suite_name(tmp_path: Path) -> None:
    path = tmp_path / "smoke.jsonl"
    path.write_text(
        '{"id":"x","suite":"other","question":"q","expected":{}}\n',
        encoding="utf-8",
    )
    with pytest.raises(DatasetError, match="不一致"):
        load_jsonl(path)


def test_load_jsonl_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "smoke.jsonl"
    line = '{"id":"dup","suite":"smoke","question":"q","expected":{}}\n'
    path.write_text(line + line, encoding="utf-8")
    with pytest.raises(DatasetError, match="重复"):
        load_jsonl(path)


def test_load_jsonl_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "smoke.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(DatasetError):
        load_jsonl(path)


@pytest.mark.asyncio
async def test_smoke_grader_passes_on_echo() -> None:
    case = EvalCase(
        id="s1",
        suite="smoke",
        question="ping",
        expected={"echo": "ping"},
    )
    observation = await SmokeProducer().produce(case)
    result = SmokeGrader().grade(case, observation)
    assert result.status is CaseStatus.PASS
    assert result.scores["smoke_pass_rate"] == 1.0


def test_smoke_grader_fails_on_mismatch() -> None:
    case = EvalCase(id="s1", suite="smoke", question="a", expected={"echo": "b"})
    result = SmokeGrader().grade(case, Observation(payload={"echo": "a"}))
    assert result.status is CaseStatus.FAIL
    assert result.scores["smoke_pass_rate"] == 0.0


@pytest.mark.asyncio
async def test_runner_writes_json_and_markdown(tmp_path: Path) -> None:
    report, run_dir = await run_eval(
        EvalConfig(
            datasets_dir=DATASETS_DIR,
            results_dir=tmp_path,
            suites=("smoke",),
            repo_root=REPO_ROOT,
        )
    )
    json_path = run_dir / "report.json"
    md_path = run_dir / "report.md"
    assert json_path.is_file()
    assert md_path.is_file()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    loaded = EvalRunReport.model_validate(payload)
    assert loaded.schema_version == "1"
    assert loaded.summary.passed == 3
    assert loaded.summary.failed == 0
    assert loaded.summary.errored == 0
    assert loaded.metrics[0].name == "smoke_pass_rate"
    assert loaded.metrics[0].value == 1.0
    assert payload["suites"][0]["cases"][0]["id"] == "smoke-echo-empty"

    markdown = md_path.read_text(encoding="utf-8")
    assert report.run_id in markdown
    assert "smoke_pass_rate" in markdown
    assert "3 通过" in markdown
    assert "失败与错误" in markdown
    assert "无。" in markdown


@pytest.mark.asyncio
async def test_runner_records_producer_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    datasets = tmp_path / "datasets"
    datasets.mkdir()
    datasets.joinpath("smoke.jsonl").write_text(
        '{"id":"boom","suite":"smoke","question":"x","expected":{"echo":"x"}}\n',
        encoding="utf-8",
    )

    class _Boom:
        async def produce(self, case: EvalCase) -> Observation:
            raise RuntimeError("boom")

    original = SUITE_HARNESS["smoke"]
    monkeypatch.setitem(
        SUITE_HARNESS, "smoke", SuiteHarness(producer=_Boom(), grader=original.grader)
    )
    report, _run_dir = await run_eval(
        EvalConfig(datasets_dir=datasets, results_dir=tmp_path / "out", suites=("smoke",))
    )

    assert report.summary.errored == 1
    assert report.suites[0].cases[0].status is CaseStatus.ERROR
    assert "boom" in (report.suites[0].cases[0].message or "")


def test_cli_smoke_writes_report(tmp_path: Path) -> None:
    code = main(["--suite", "smoke", "--out", str(tmp_path), "--datasets", str(DATASETS_DIR)])
    assert code == 0
    runs = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert len(runs) == 1
    assert (runs[0] / "report.json").is_file()
    assert (runs[0] / "report.md").is_file()


def test_cli_unknown_suite() -> None:
    assert main(["--suite", "not_a_suite"]) == 2


def test_cli_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--list", "--datasets", str(DATASETS_DIR)]) == 0
    output = capsys.readouterr().out
    assert "smoke" in output
    assert "yes" in output


def test_cli_failing_case_exits_1(tmp_path: Path) -> None:
    datasets = tmp_path / "datasets"
    datasets.mkdir()
    datasets.joinpath("smoke.jsonl").write_text(
        '{"id":"bad","suite":"smoke","question":"a","expected":{"echo":"b"}}\n',
        encoding="utf-8",
    )
    code = main(["--suite", "smoke", "--out", str(tmp_path / "out"), "--datasets", str(datasets)])
    assert code == 1


def test_make_run_id_is_filesystem_safe() -> None:
    started = datetime(2026, 9, 11, 7, 55, 12, tzinfo=UTC)
    assert make_run_id(started, "smoke extra") == "20260911T075512Z_smoke-extra"
    assert make_run_id(started, "+++") == "20260911T075512Z_run"


def test_summarize_counts_excludes_skip_from_pass_rate() -> None:
    cases = [
        CaseResult(
            id="a",
            suite="s",
            question="q",
            status=CaseStatus.PASS,
            scores={},
            expected={},
            duration_ms=0,
            grader="smoke",
        ),
        CaseResult(
            id="b",
            suite="s",
            question="q",
            status=CaseStatus.SKIP,
            scores={},
            expected={},
            duration_ms=0,
            grader="smoke",
        ),
    ]
    summary = summarize_counts(cases)
    assert summary.pass_rate == 1.0
    assert summary.skipped == 1


def test_markdown_lists_failures() -> None:
    started = datetime(2026, 9, 11, 8, 0, tzinfo=UTC)
    failed = CaseResult(
        id="bad",
        suite="smoke",
        question="a",
        status=CaseStatus.FAIL,
        scores={"smoke_pass_rate": 0.0},
        expected={"echo": "b"},
        actual={"echo": "a"},
        message="echo 不一致",
        duration_ms=1,
        grader="smoke",
    )
    report = EvalRunReport(
        run_id="demo",
        started_at=started,
        finished_at=started,
        duration_ms=0,
        git=GitSnapshot(sha="abc", dirty=False),
        mode="fixture",
        label="demo",
        suites_requested=["smoke"],
        suites=[
            SuiteResult(
                name="smoke",
                grader="smoke",
                cases=[failed],
                metrics=[],
                passed=0,
                failed=1,
                errored=0,
                skipped=0,
            )
        ],
        metrics=[],
        summary=RunSummary(
            cases_total=1,
            passed=0,
            failed=1,
            errored=0,
            skipped=0,
            pass_rate=0.0,
        ),
    )
    markdown = render_markdown(report)
    assert "`bad`" in markdown
    assert "echo 不一致" in markdown
