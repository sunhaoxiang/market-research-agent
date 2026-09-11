"""Eval 用例与归档报告的契约。

结果 JSON 的真源在这里。P7-2 起 grader 往 `scores` 里填 [DP §19.2] 的维度名，
不要另起一套字段——跨 run 对比只认 `schema_version` + `metrics[].name`。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EvalModel(BaseModel):
    """Eval 侧模型：未知字段直接报错，避免归档时默默丢掉指标。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class CaseStatus(StrEnum):
    PASS = "pass"  # noqa: S105
    FAIL = "fail"
    ERROR = "error"
    SKIP = "skip"


class MetricName(StrEnum):
    """[DP §19.2] 评估维度。P7-1 只产出 `smoke_pass_rate`。"""

    SMOKE_PASS_RATE = "smoke_pass_rate"  # noqa: S105
    AGENT_ROUTING_ACCURACY = "agent_routing_accuracy"
    TOOL_SELECTION_RECALL = "tool_selection_recall"
    TOOL_SELECTION_PRECISION = "tool_selection_precision"
    CITATION_COVERAGE = "citation_coverage"
    CITATION_VALIDITY = "citation_validity"
    HALLUCINATION_RATE = "hallucination_rate"
    EPISTEMIC_ACCURACY = "epistemic_accuracy"
    JUDGE_AGREEMENT = "judge_agreement"
    CONFLICT_DETECTION_RATE = "conflict_detection_rate"
    REPORT_COMPLETENESS = "report_completeness"
    NUMERIC_ACCURACY = "numeric_accuracy"
    LATENCY_P50_S = "latency_p50_s"
    COST_PER_RUN = "cost_per_run"


class EvalCase(EvalModel):
    """datasets/*.jsonl 的一行。"""

    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    suite: str
    question: str
    expected: dict[str, Any]
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    fixtures: dict[str, Any] | None = None


class Observation(EvalModel):
    """Producer 输出、grader 输入。P7-2 起 payload 形状按 suite 约定。"""

    payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int = 0


class GradeResult(EvalModel):
    status: CaseStatus
    scores: dict[str, float] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None


class CaseResult(EvalModel):
    id: str
    suite: str
    question: str
    status: CaseStatus
    scores: dict[str, float]
    expected: dict[str, Any]
    actual: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
    duration_ms: int
    grader: str
    tags: list[str] = Field(default_factory=list)


class MetricScore(EvalModel):
    name: str
    value: float
    n: int


class SuiteResult(EvalModel):
    name: str
    grader: str
    cases: list[CaseResult]
    metrics: list[MetricScore]
    passed: int
    failed: int
    errored: int
    skipped: int


class RunSummary(EvalModel):
    cases_total: int
    passed: int
    failed: int
    errored: int
    skipped: int
    pass_rate: float | None


class GitSnapshot(EvalModel):
    sha: str | None
    dirty: bool


class EvalRunReport(EvalModel):
    schema_version: Literal["1"] = "1"
    run_id: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    git: GitSnapshot
    mode: Literal["fixture", "live"]
    label: str
    model_ids: dict[str, str] = Field(default_factory=dict)
    suites_requested: list[str]
    suites: list[SuiteResult]
    metrics: list[MetricScore]
    summary: RunSummary
