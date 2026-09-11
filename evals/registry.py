"""suite 名 → producer / grader。P7-2 起在此追加，不要让 runner 硬编码套件列表。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from evals.graders.deterministic import DeterministicGrader
from evals.graders.llm_judge import JudgeProducer, LlmJudgeGrader
from evals.graders.smoke import SmokeGrader, SmokeProducer
from evals.producers import (
    ExternalReplayProducer,
    FixtureProducer,
    IntentRoutingProducer,
    PromptInjectionProducer,
)
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


_DETERMINISTIC = DeterministicGrader()
_FIXTURE = FixtureProducer()
_REPLAY = ExternalReplayProducer()
_LLM_JUDGE = LlmJudgeGrader()
_JUDGE_PRODUCER = JudgeProducer()

SUITE_HARNESS: dict[str, SuiteHarness] = {
    "smoke": SuiteHarness(producer=SmokeProducer(), grader=SmokeGrader()),
    "intent_routing": SuiteHarness(producer=IntentRoutingProducer(), grader=_DETERMINISTIC),
    "tool_selection": SuiteHarness(producer=_FIXTURE, grader=_DETERMINISTIC),
    "crypto_project": SuiteHarness(producer=_REPLAY, grader=_DETERMINISTIC),
    "stock_analysis": SuiteHarness(producer=_REPLAY, grader=_DETERMINISTIC),
    "financial_report": SuiteHarness(producer=_REPLAY, grader=_DETERMINISTIC),
    "fact_check": SuiteHarness(producer=_JUDGE_PRODUCER, grader=_LLM_JUDGE),
    "epistemic_labeling": SuiteHarness(producer=_JUDGE_PRODUCER, grader=_LLM_JUDGE),
    "prompt_injection": SuiteHarness(
        producer=PromptInjectionProducer(),
        grader=_DETERMINISTIC,
    ),
}
