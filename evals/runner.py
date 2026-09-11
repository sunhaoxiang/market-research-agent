"""跑 eval，输出 JSON + Markdown 报告（[DP §19]）。

用法：
    ./scripts/eval.sh --suite smoke
    ./scripts/eval.sh --list
    ./scripts/eval.sh --compare run_a,run_b
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Literal

from agent_service.models.registry import ModelRegistry
from agent_service.schemas.common import ModelRole
from evals.archive import allocate_run_dir, make_run_id, summarize_counts, write_report
from evals.compare import MIN_COMPARE_RUNS, CompareError, resolve_run_path, run_compare
from evals.datasets_io import DatasetError, list_dataset_files, load_jsonl
from evals.graders.llm_judge import install_default_live_judge, set_live_judge
from evals.paths import DATASETS_DIR, LOCAL_RESULTS_DIR, REPO_ROOT
from evals.registry import SUITE_HARNESS
from evals.schemas import (
    CaseResult,
    CaseStatus,
    EvalCase,
    EvalRunReport,
    GitSnapshot,
    GradeResult,
    MetricScore,
    RunSummary,
    SuiteResult,
)


@dataclass(frozen=True, slots=True)
class EvalConfig:
    datasets_dir: Path
    results_dir: Path
    suites: tuple[str, ...] | None
    mode: Literal["fixture", "live"] = "fixture"
    label: str | None = None
    model_ids: dict[str, str] | None = None
    repo_root: Path = REPO_ROOT


class EvalError(Exception):
    """CLI 可预期的失败（未知 suite、没有可跑的用例）。"""


def snapshot_model_ids() -> dict[str, str]:
    """当前角色 → model_id，不打 LLM。"""
    registry = ModelRegistry()
    return {role.value: registry.model_id_for_role(role) for role in ModelRole}


def git_snapshot(repo: Path) -> GitSnapshot:
    git = which("git")
    if git is None:
        return GitSnapshot(sha=None, dirty=False)
    sha = _git_output(git, ["rev-parse", "HEAD"], repo)
    porcelain = _git_output(git, ["status", "--porcelain"], repo)
    dirty = bool(porcelain.strip()) if porcelain is not None else False
    return GitSnapshot(sha=sha, dirty=dirty)


def _git_output(git: str, args: list[str], cwd: Path) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603
            [git, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def aggregate_metrics(cases: list[CaseResult]) -> list[MetricScore]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for case in cases:
        if case.status is CaseStatus.SKIP:
            continue
        for name, value in case.scores.items():
            buckets[name].append(value)
    return [
        MetricScore(name=name, value=sum(values) / len(values), n=len(values))
        for name, values in sorted(buckets.items())
        if values
    ]


def _now() -> datetime:
    return datetime.now(UTC)


def resolve_suites(config: EvalConfig) -> list[str]:
    files = {path.stem: path for path in list_dataset_files(config.datasets_dir)}
    if config.suites is None:
        return [name for name in sorted(files) if name in SUITE_HARNESS]
    missing_harness = [name for name in config.suites if name not in SUITE_HARNESS]
    if missing_harness:
        available = ", ".join(sorted(SUITE_HARNESS)) or "(none)"
        raise EvalError(f"未注册的 suite: {', '.join(missing_harness)}。已注册：{available}")
    missing_files = [name for name in config.suites if name not in files]
    if missing_files:
        raise EvalError(f"找不到数据集：{', '.join(missing_files)}")
    return list(config.suites)


def load_suite_cases(config: EvalConfig, suite: str) -> list[EvalCase]:
    path = config.datasets_dir / f"{suite}.jsonl"
    return load_jsonl(path)


async def _grade_case(case: EvalCase, harness_name: str) -> CaseResult:
    harness = SUITE_HARNESS[harness_name]
    started = time.perf_counter()
    try:
        observation = await harness.producer.produce(case)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return CaseResult(
            id=case.id,
            suite=case.suite,
            question=case.question,
            status=CaseStatus.ERROR,
            scores={},
            expected=case.expected,
            actual={},
            message=f"producer 失败：{exc}",
            duration_ms=duration_ms,
            grader=harness.grader.name,
            tags=case.tags,
        )
    if observation.error:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return CaseResult(
            id=case.id,
            suite=case.suite,
            question=case.question,
            status=CaseStatus.ERROR,
            scores={},
            expected=case.expected,
            actual=observation.payload,
            message=observation.error,
            duration_ms=duration_ms,
            grader=harness.grader.name,
            tags=case.tags,
        )
    grade: GradeResult = harness.grader.grade(case, observation)
    duration_ms = int((time.perf_counter() - started) * 1000)
    if observation.duration_ms:
        duration_ms = observation.duration_ms
    return CaseResult(
        id=case.id,
        suite=case.suite,
        question=case.question,
        status=grade.status,
        scores=grade.scores,
        expected=case.expected,
        actual=grade.actual,
        message=grade.message,
        duration_ms=duration_ms,
        grader=harness.grader.name,
        tags=case.tags,
    )


async def run_eval(config: EvalConfig) -> tuple[EvalRunReport, Path]:
    set_live_judge(None)
    if config.mode == "live":
        try:
            install_default_live_judge()
        except RuntimeError as exc:
            raise EvalError(str(exc)) from exc
    try:
        return await _run_eval(config)
    finally:
        set_live_judge(None)


async def _run_eval(config: EvalConfig) -> tuple[EvalRunReport, Path]:
    started_at = _now()
    suites = resolve_suites(config)
    if not suites:
        raise EvalError("没有可跑的 suite（需要数据集文件且已在 registry 注册）")
    label = config.label or "+".join(suites)
    run_id = make_run_id(started_at, label)

    suite_results: list[SuiteResult] = []
    all_cases: list[CaseResult] = []
    for suite in suites:
        cases = load_suite_cases(config, suite)
        graded: list[CaseResult] = []
        for case in cases:
            graded.append(await _grade_case(case, suite))
        counts = summarize_counts(graded)
        suite_results.append(
            SuiteResult(
                name=suite,
                grader=SUITE_HARNESS[suite].grader.name,
                cases=graded,
                metrics=aggregate_metrics(graded),
                passed=counts.passed,
                failed=counts.failed,
                errored=counts.errored,
                skipped=counts.skipped,
            )
        )
        all_cases.extend(graded)

    finished_at = _now()
    summary = summarize_counts(all_cases)
    report = EvalRunReport(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=max(0, int((finished_at - started_at).total_seconds() * 1000)),
        git=git_snapshot(config.repo_root),
        mode=config.mode,
        label=label,
        model_ids=config.model_ids if config.model_ids is not None else snapshot_model_ids(),
        suites_requested=suites,
        suites=suite_results,
        metrics=aggregate_metrics(all_cases),
        summary=summary,
    )
    run_dir = allocate_run_dir(config.results_dir, report.run_id)
    if run_dir.name != report.run_id:
        report = report.model_copy(update={"run_id": run_dir.name})
    write_report(report, run_dir)
    return report, run_dir


def _print_list(datasets_dir: Path) -> int:
    files = list_dataset_files(datasets_dir)
    if not files:
        print(f"没有数据集：{datasets_dir}")
        return 0
    print("suite              cases  harness")
    for path in files:
        try:
            n_cases = len(load_jsonl(path))
            cases_cell = str(n_cases)
        except DatasetError as exc:
            cases_cell = f"error ({exc})"
        harness = "yes" if path.stem in SUITE_HARNESS else "no"
        print(f"{path.stem:<18} {cases_cell:<6} {harness}")
    return 0


def _parse_suites(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    suites = tuple(part.strip() for part in raw.split(",") if part.strip())
    return suites or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        dest="suites",
        default=None,
        help="逗号分隔的 suite 名；默认跑所有已注册且有数据集的 suite",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"结果父目录（默认 {LOCAL_RESULTS_DIR}）",
    )
    parser.add_argument("--label", default=None, help="写入 run_id 的标签，默认用 suite 名")
    parser.add_argument(
        "--mode",
        choices=("fixture", "live"),
        default="fixture",
        help="fixture=录制数据与冻结的外部 API；live=只把 LLM judge 切到 FAST 模型",
    )
    parser.add_argument("--list", action="store_true", help="列出数据集与是否已注册 harness")
    parser.add_argument(
        "--compare",
        default=None,
        help="逗号分隔的 report.json 或 run 目录，生成跨模型对比表并归档",
    )
    parser.add_argument(
        "--datasets",
        type=Path,
        default=DATASETS_DIR,
        help="数据集目录",
    )
    return parser


def _main_compare(args: argparse.Namespace) -> int:
    raw = [part.strip() for part in str(args.compare).split(",") if part.strip()]
    if len(raw) < MIN_COMPARE_RUNS:
        print("✖ --compare 至少需要两份报告")
        return 2
    try:
        paths = [resolve_run_path(item) for item in raw]
        report, run_dir = run_compare(
            paths,
            out_dir=args.out or LOCAL_RESULTS_DIR,
            label=args.label,
            git=git_snapshot(REPO_ROOT),
        )
    except CompareError as exc:
        print(f"✖ {exc}")
        return 2
    by_id = {item.run_id: item.label for item in report.runs}
    winners = ", ".join(by_id[run_id] for run_id in report.winner_run_ids)
    print(f"✓ 对比完成  {report.summary}")
    print(f"  最优  {winners}")
    print(f"  JSON  {run_dir / 'comparison.json'}")
    print(f"  MD    {run_dir / 'comparison.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    datasets_dir: Path = args.datasets
    if args.list:
        return _print_list(datasets_dir)
    if args.compare:
        return _main_compare(args)

    config = EvalConfig(
        datasets_dir=datasets_dir,
        results_dir=args.out or LOCAL_RESULTS_DIR,
        suites=_parse_suites(args.suites),
        mode=args.mode,
        label=args.label,
    )
    try:
        report, run_dir = asyncio.run(run_eval(config))
    except DatasetError as exc:
        print(f"✖ 数据集错误：{exc}")
        return 2
    except EvalError as exc:
        print(f"✖ {exc}")
        return 2

    summary: RunSummary = report.summary
    print(
        f"✓ eval 完成  {summary.passed} passed / {summary.failed} failed / "
        f"{summary.errored} error / {summary.skipped} skipped"
    )
    print(f"  JSON  {run_dir / 'report.json'}")
    print(f"  MD    {run_dir / 'report.md'}")
    if summary.failed or summary.errored:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
