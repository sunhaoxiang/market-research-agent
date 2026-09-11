"""把 EvalRunReport 写成 JSON + Markdown，目录按 (时间, label) 归档。"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from evals.schemas import CaseResult, CaseStatus, EvalRunReport, MetricScore, RunSummary

_LABEL_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_label(label: str) -> str:
    cleaned = _LABEL_RE.sub("-", label.strip()).strip("-._")
    return cleaned or "run"


def make_run_id(started_at: datetime, label: str) -> str:
    stamp = started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{safe_label(label)}"


def allocate_run_dir(parent: Path, run_id: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    candidate = parent / run_id
    if not candidate.exists():
        return candidate
    for index in range(2, 100):
        alt = parent / f"{run_id}_{index}"
        if not alt.exists():
            return alt
    msg = f"无法在 {parent} 下为 {run_id} 分配目录"
    raise RuntimeError(msg)


def write_report(report: EvalRunReport, run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    (run_dir / "report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (run_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: EvalRunReport) -> str:
    summary = report.summary
    pass_rate = _fmt_rate(summary.pass_rate)
    git = report.git.sha or "(unknown)"
    if report.git.dirty:
        git += " dirty"

    lines = [
        f"# Eval 报告 `{report.run_id}`",
        "",
        f"- schema: `{report.schema_version}`",
        f"- 时间: {report.started_at.isoformat()} → {report.finished_at.isoformat()}",
        f"- 耗时: {report.duration_ms}ms",
        f"- git: `{git}`",
        f"- 模式: `{report.mode}`",
        f"- label: `{report.label}`",
        (
            f"- 用例: {summary.passed} 通过 / {summary.failed} 失败 / "
            f"{summary.errored} 错误 / {summary.skipped} 跳过"
            f"（pass_rate {pass_rate}，共 {summary.cases_total}）"
        ),
        "",
        "## 指标",
        "",
    ]
    lines.extend(_metrics_table(report.metrics))
    lines.append("")

    for suite in report.suites:
        lines.append(f"## {suite.name}（grader `{suite.grader}`）")
        lines.append("")
        lines.append(
            f"{suite.passed} 通过 / {suite.failed} 失败 / "
            f"{suite.errored} 错误 / {suite.skipped} 跳过"
        )
        lines.append("")
        lines.extend(_metrics_table(suite.metrics))
        lines.append("")
        lines.append("| ID | 状态 | 耗时 |")
        lines.append("| --- | --- | --- |")
        for case in suite.cases:
            lines.append(f"| `{case.id}` | {case.status} | {case.duration_ms}ms |")
        lines.append("")

    failures = [
        case
        for suite in report.suites
        for case in suite.cases
        if case.status in {CaseStatus.FAIL, CaseStatus.ERROR}
    ]
    lines.append("## 失败与错误")
    lines.append("")
    if not failures:
        lines.append("无。")
        lines.append("")
    else:
        for case in failures:
            lines.extend(_failure_block(case))

    return "\n".join(lines)


def _metrics_table(metrics: list[MetricScore]) -> list[str]:
    if not metrics:
        return ["（本 run 没有指标）", ""]
    rows = ["| 指标 | 分数 | n |", "| --- | --- | --- |"]
    rows.extend(f"| `{item.name}` | {item.value:.4f} | {item.n} |" for item in metrics)
    return rows


def _failure_block(case: CaseResult) -> list[str]:
    message = case.message or "(无说明)"
    return [
        f"### `{case.id}`（{case.status}）",
        "",
        f"- suite: `{case.suite}`",
        f"- question: {case.question}",
        f"- message: {message}",
        "",
    ]


def _fmt_rate(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1%}"


def summarize_counts(cases: list[CaseResult]) -> RunSummary:
    passed = sum(1 for case in cases if case.status is CaseStatus.PASS)
    failed = sum(1 for case in cases if case.status is CaseStatus.FAIL)
    errored = sum(1 for case in cases if case.status is CaseStatus.ERROR)
    skipped = sum(1 for case in cases if case.status is CaseStatus.SKIP)
    scored = passed + failed
    return RunSummary(
        cases_total=len(cases),
        passed=passed,
        failed=failed,
        errored=errored,
        skipped=skipped,
        pass_rate=(passed / scored) if scored else None,
    )
