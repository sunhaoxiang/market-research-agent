"""跨模型 eval 对比（P7-8 / [DP §19.3]）。

读多个 `schema_version=1` 的 `report.json`，按 `metrics[].name` 对齐，
写出 `comparison.json` + `comparison.md`。核心指标是幻觉率与引用覆盖率；
综合分只在各 run 都有的指标上算。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from evals.archive import allocate_run_dir, make_run_id
from evals.paths import FIXTURES_DIR, LOCAL_RESULTS_DIR, REPO_ROOT, RESULTS_DIR
from evals.schemas import (
    CompareMetric,
    CompareRun,
    EvalCompareReport,
    EvalRunReport,
    GitSnapshot,
    MetricName,
)

LOWER_BETTER: frozenset[str] = frozenset(
    {
        MetricName.HALLUCINATION_RATE.value,
        MetricName.LATENCY_P50_S.value,
        MetricName.COST_PER_RUN.value,
    }
)

PASS_RATE_NAME = "pass_rate"  # noqa: S105
_TIE_EPS = 1e-9
MIN_COMPARE_RUNS = 2

# [DP §19.3] 幻觉率与引用覆盖率优先于速度。
METRIC_WEIGHTS: dict[str, float] = {
    MetricName.HALLUCINATION_RATE.value: 3.0,
    MetricName.CITATION_COVERAGE.value: 3.0,
    MetricName.CITATION_VALIDITY.value: 2.0,
    MetricName.NUMERIC_ACCURACY.value: 2.0,
    MetricName.EPISTEMIC_ACCURACY.value: 2.0,
    PASS_RATE_NAME: 2.0,
    MetricName.AGENT_ROUTING_ACCURACY.value: 1.0,
    MetricName.TOOL_SELECTION_RECALL.value: 1.0,
    MetricName.TOOL_SELECTION_PRECISION.value: 1.0,
    MetricName.CONFLICT_DETECTION_RATE.value: 1.0,
    MetricName.REPORT_COMPLETENESS.value: 1.0,
    MetricName.INJECTION_DETECTION_RATE.value: 1.0,
    MetricName.JUDGE_AGREEMENT.value: 0.5,
    MetricName.SMOKE_PASS_RATE.value: 0.5,
    MetricName.LATENCY_P50_S.value: 0.5,
    MetricName.COST_PER_RUN.value: 0.5,
}


class CompareError(Exception):
    """CLI 可预期的对比失败。"""


def polarity_for(name: str) -> Literal["higher", "lower"]:
    return "lower" if name in LOWER_BETTER else "higher"


def load_run_report(path: Path) -> EvalRunReport:
    target = path / "report.json" if path.is_dir() else path
    if not target.is_file():
        raise CompareError(f"找不到 report.json：{path}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CompareError(f"无法解析 {target}：{exc}") from exc
    if not isinstance(payload, dict):
        raise CompareError(f"eval 报告必须是对象：{target}")
    if payload.get("kind") == "compare":
        raise CompareError(f"{target} 是对比报告，不是单次 eval run")
    try:
        report = EvalRunReport.model_validate(payload)
    except Exception as exc:
        raise CompareError(f"无效的 eval 报告 {target}：{exc}") from exc
    if report.schema_version != "1":
        raise CompareError(f"{target} 的 schema_version={report.schema_version!r}，对比只认 '1'")
    return report


def resolve_run_path(raw: str) -> Path:
    candidate = Path(raw)
    options = (
        [candidate]
        if candidate.is_absolute()
        else [
            Path.cwd() / candidate,
            REPO_ROOT / candidate,
            LOCAL_RESULTS_DIR / candidate,
            RESULTS_DIR / candidate,
            FIXTURES_DIR / "compare" / candidate,
        ]
    )
    for path in options:
        resolved = path.resolve() if path.exists() else path
        if resolved.is_file() or (resolved.is_dir() and (resolved / "report.json").is_file()):
            return resolved
    raise CompareError(f"找不到 eval 报告：{raw}")


def metric_map(report: EvalRunReport) -> dict[str, float]:
    values = {item.name: item.value for item in report.metrics}
    if PASS_RATE_NAME not in values and report.summary.pass_rate is not None:
        values[PASS_RATE_NAME] = report.summary.pass_rate
    if MetricName.LATENCY_P50_S.value not in values:
        latency = _latency_p50_s(report)
        if latency is not None:
            values[MetricName.LATENCY_P50_S.value] = latency
    return values


def compare_reports(
    reports: list[EvalRunReport],
    *,
    created_at: datetime | None = None,
    git: GitSnapshot | None = None,
    compare_id: str | None = None,
) -> EvalCompareReport:
    if len(reports) < MIN_COMPARE_RUNS:
        raise CompareError("对比至少需要两份 eval 报告")
    run_ids = [report.run_id for report in reports]
    if len(set(run_ids)) != len(run_ids):
        raise CompareError("对比的 run_id 重复")
    labels = _column_labels(reports)
    maps = [metric_map(report) for report in reports]
    names = _metric_union(maps)
    rows = [_metric_row(name, run_ids, maps) for name in names]
    scores = _quality_scores(run_ids, maps)
    ranked = _ranks(scores)
    best_score = max(scores.values())
    winners = [run_id for run_id, score in scores.items() if abs(score - best_score) < _TIE_EPS]
    runs = [
        CompareRun(
            run_id=report.run_id,
            label=labels[index],
            model_ids=dict(report.model_ids),
            git=report.git,
            mode=report.mode,
            pass_rate=report.summary.pass_rate,
            quality_score=scores[report.run_id],
            rank=ranked[report.run_id],
        )
        for index, report in enumerate(reports)
    ]
    stamp = created_at or datetime.now(UTC)
    report_id = compare_id or make_run_id(stamp, "compare-" + "-".join(labels))
    return EvalCompareReport(
        compare_id=report_id,
        created_at=stamp,
        git=git or GitSnapshot(sha=None, dirty=False),
        run_ids=run_ids,
        runs=runs,
        metrics=rows,
        winner_run_ids=winners,
        summary=_summary_line(runs, winners),
    )


def write_comparison(report: EvalCompareReport, run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    (run_dir / "comparison.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (run_dir / "comparison.md").write_text(render_compare_markdown(report), encoding="utf-8")


def render_compare_markdown(report: EvalCompareReport) -> str:
    by_id = {item.run_id: item for item in report.runs}
    headers = [by_id[run_id].label for run_id in report.run_ids]
    lines = [
        f"# Eval 对比 `{report.compare_id}`",
        "",
        f"- schema: `{report.schema_version}` kind=`compare`",
        f"- 时间: {report.created_at.isoformat()}",
        f"- git: `{_fmt_git(report.git)}`",
        "- 核心指标：幻觉率（越低越好）、引用覆盖率（越高越好）",
        f"- 结论：{report.summary}",
        "",
        "## 综合排名",
        "",
        "| 排名 | 标签 | 综合分 | pass_rate | 模型 |",
        "| --- | --- | --- | --- | --- |",
    ]
    ordered = sorted(report.runs, key=lambda item: (item.rank, item.label))
    for item in ordered:
        models = ", ".join(f"{role}={model}" for role, model in sorted(item.model_ids.items()))
        lines.append(
            f"| {item.rank} | `{item.label}` | {item.quality_score:.4f} | "
            f"{_fmt_score(item.pass_rate)} | {models or '—'} |"
        )
    lines.extend(["", "## 指标对照", ""])
    header = "| 指标 | 极性 | " + " | ".join(f"`{name}`" for name in headers) + " | 最优 |"
    sep = "| --- | --- | " + " | ".join("---" for _ in headers) + " | --- |"
    lines.extend([header, sep])
    for row in report.metrics:
        cells = [_fmt_metric_cell(row, run_id) for run_id in report.run_ids]
        winners = ", ".join(f"`{by_id[run_id].label}`" for run_id in row.winner_run_ids)
        polarity = "低更好" if row.polarity == "lower" else "高更好"
        lines.append(
            f"| `{row.name}` | {polarity} | " + " | ".join(cells) + f" | {winners or '—'} |"
        )
    lines.append("")
    return "\n".join(lines)


def run_compare(
    paths: list[Path],
    *,
    out_dir: Path,
    label: str | None = None,
    git: GitSnapshot | None = None,
    created_at: datetime | None = None,
) -> tuple[EvalCompareReport, Path]:
    reports = [load_run_report(path) for path in paths]
    stamp = created_at or datetime.now(UTC)
    compare_id = make_run_id(stamp, label or "compare")
    report = compare_reports(reports, created_at=stamp, git=git, compare_id=compare_id)
    run_dir = allocate_run_dir(out_dir, report.compare_id)
    if run_dir.name != report.compare_id:
        report = report.model_copy(update={"compare_id": run_dir.name})
    write_comparison(report, run_dir)
    return report, run_dir


def _column_labels(reports: list[EvalRunReport]) -> list[str]:
    labels = [report.label.strip() or report.run_id for report in reports]
    if len(set(labels)) == len(labels):
        return labels
    return [f"{label} ({report.run_id})" for label, report in zip(labels, reports, strict=True)]


def _metric_union(maps: list[dict[str, float]]) -> list[str]:
    names: set[str] = set()
    for item in maps:
        names.update(item)
    preferred = [name.value for name in MetricName] + [PASS_RATE_NAME]
    ordered = [name for name in preferred if name in names]
    extra = sorted(names.difference(ordered))
    return ordered + extra


def _metric_row(name: str, run_ids: list[str], maps: list[dict[str, float]]) -> CompareMetric:
    values: dict[str, float | None] = {
        run_id: mapping.get(name) for run_id, mapping in zip(run_ids, maps, strict=True)
    }
    present = [value for value in values.values() if value is not None]
    polarity = polarity_for(name)
    winners: list[str] = []
    if present:
        best = min(present) if polarity == "lower" else max(present)
        winners = [
            run_id
            for run_id, value in values.items()
            if value is not None and abs(value - best) < _TIE_EPS
        ]
    return CompareMetric(name=name, polarity=polarity, values=values, winner_run_ids=winners)


def _quality_scores(run_ids: list[str], maps: list[dict[str, float]]) -> dict[str, float]:
    shared = set(maps[0])
    for mapping in maps[1:]:
        shared &= set(mapping)
    weighted: list[tuple[str, float]] = []
    for name in shared:
        weight = METRIC_WEIGHTS.get(name, 1.0)
        if weight <= 0:
            continue
        weighted.append((name, weight))
    if not weighted:
        return dict.fromkeys(run_ids, 0.0)
    totals = dict.fromkeys(run_ids, 0.0)
    weight_sum = 0.0
    for name, weight in weighted:
        series = [mapping[name] for mapping in maps]
        scaled = _minmax(series, lower_better=name in LOWER_BETTER)
        weight_sum += weight
        for run_id, value in zip(run_ids, scaled, strict=True):
            totals[run_id] += value * weight
    if weight_sum <= 0:
        return dict.fromkeys(run_ids, 0.0)
    return {run_id: total / weight_sum for run_id, total in totals.items()}


def _minmax(values: list[float], *, lower_better: bool) -> list[float]:
    lo = min(values)
    hi = max(values)
    if hi - lo < _TIE_EPS:
        return [1.0 for _ in values]
    scaled = [(item - lo) / (hi - lo) for item in values]
    if lower_better:
        return [1.0 - item for item in scaled]
    return scaled


def _ranks(scores: dict[str, float]) -> dict[str, int]:
    ordered = sorted(scores, key=lambda run_id: (-scores[run_id], run_id))
    ranks: dict[str, int] = {}
    rank = 0
    last: float | None = None
    for index, run_id in enumerate(ordered, start=1):
        score = scores[run_id]
        if last is None or abs(score - last) >= _TIE_EPS:
            rank = index
            last = score
        ranks[run_id] = rank
    return ranks


def _summary_line(runs: list[CompareRun], winners: list[str]) -> str:
    by_id = {item.run_id: item for item in runs}
    names = [f"`{by_id[run_id].label}`" for run_id in winners]
    if len(names) == 1:
        return f"**{names[0]} 综合更好**"
    return "**并列最优**：" + "、".join(names)


def _latency_p50_s(report: EvalRunReport) -> float | None:
    samples = [case.duration_ms / 1000.0 for suite in report.suites for case in suite.cases]
    if not samples:
        return None
    ordered = sorted(samples)
    return ordered[(len(ordered) - 1) // 2]


def _fmt_git(git: GitSnapshot) -> str:
    sha = git.sha or "(unknown)"
    return f"{sha} dirty" if git.dirty else sha


def _fmt_score(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.4f}"


def _fmt_metric_cell(row: CompareMetric, run_id: str) -> str:
    value = row.values.get(run_id)
    text = _fmt_score(value)
    if run_id in row.winner_run_ids and value is not None:
        return f"**{text}**"
    return text
