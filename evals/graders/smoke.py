"""离线冒烟：把问题原样回显，用来验证 runner 与归档格式。"""

from __future__ import annotations

from evals.schemas import CaseStatus, EvalCase, GradeResult, MetricName, Observation


class SmokeProducer:
    async def produce(self, case: EvalCase) -> Observation:
        return Observation(payload={"echo": case.question})


class SmokeGrader:
    name = "smoke"

    def grade(self, case: EvalCase, observation: Observation) -> GradeResult:
        actual = {"echo": observation.payload.get("echo")}
        expected_echo = case.expected.get("echo")
        passed = actual["echo"] == expected_echo
        return GradeResult(
            status=CaseStatus.PASS if passed else CaseStatus.FAIL,
            scores={str(MetricName.SMOKE_PASS_RATE): 1.0 if passed else 0.0},
            actual=actual,
            message=None if passed else f"echo 不一致：got {actual['echo']!r}",
        )
