"""suite 名 → producer / grader。P7-2 起在此追加，不要让 runner 硬编码套件列表。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from evals.graders.smoke import SmokeGrader, SmokeProducer
from evals.schemas import EvalCase, GradeResult, Observation


class Producer(Protocol):
    async def produce(self, case: EvalCase) -> Observation: ...


class Grader(Protocol):
    name: str

    def grade(self, case: EvalCase, observation: Observation) -> GradeResult: ...


@dataclass(frozen=True, slots=True)
class SuiteHarness:
    producer: Producer
    grader: Grader


SUITE_HARNESS: dict[str, SuiteHarness] = {
    "smoke": SuiteHarness(producer=SmokeProducer(), grader=SmokeGrader()),
}
