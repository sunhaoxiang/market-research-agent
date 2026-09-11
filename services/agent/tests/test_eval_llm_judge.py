"""P7-3 LLM-as-judge：幻觉率、认知类型、与金标一致率。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents import Agent, ModelSettings
from agents.testing import ScriptedModel, assistant_message

from agent_service.models.capabilities import ModelCapabilities, StructuredOutputMode
from agent_service.models.structured_output import build_strategy
from agent_service.tools.web.untrusted import detect_injection
from evals.datasets_io import load_jsonl
from evals.graders.llm_judge import (
    AGREEMENT_MIN,
    JudgeBatch,
    LiveClaimJudge,
    LlmJudgeGrader,
    heuristic_epistemic,
    heuristic_judge,
    heuristic_supported,
    set_live_judge,
)
from evals.paths import DATASETS_DIR
from evals.runner import EvalConfig, run_eval
from evals.schemas import CaseStatus, EvalCase, Observation


def _caps() -> ModelCapabilities:
    return ModelCapabilities(
        tool_calling=True,
        parallel_tool_calls=True,
        structured_output=StructuredOutputMode.JSON_MODE,
        streaming=True,
        reasoning=False,
        vision=False,
        context_window=100_000,
        max_output_tokens=8_000,
    )


def test_heuristic_flags_number_mismatch() -> None:
    source = "Hyperliquid TVL 约 18 亿美元 across perps."
    assert heuristic_supported("HYPE TVL 约 18 亿美元", source) is True
    assert heuristic_supported("HYPE TVL 已超过 100 亿美元", source) is False
    assert heuristic_supported("该协议已经完成收购", "") is False


def test_heuristic_epistemic_cues() -> None:
    assert heuristic_epistemic("HYPE TVL 约 18 亿美元", has_source=True) == "source_backed_fact"
    assert heuristic_epistemic("比特币是一种加密资产", has_source=False) == "fact"
    assert heuristic_epistemic("高利润率意味着定价权仍在", has_source=False) == "analysis"
    assert heuristic_epistemic("成交量下降可能反映需求走弱", has_source=False) == "inference"
    assert heuristic_epistemic("预计下一季营收将继续增长", has_source=False) == "prediction"
    assert heuristic_epistemic("我认为当前估值偏贵，不建议追高", has_source=False) == "opinion"


def test_grader_agreement_and_hallucination() -> None:
    grader = LlmJudgeGrader()
    case = EvalCase.model_validate(
        {
            "id": "c1",
            "suite": "fact_check",
            "question": "q",
            "expected": {"supported": False, "epistemic_type": "source_backed_fact"},
        }
    )
    observation = Observation(
        payload={
            "claims": [
                {
                    "id": "c1",
                    "text": "HYPE TVL 已超过 100 亿美元",
                    "source_ids": ["s1"],
                }
            ],
            "sources": [
                {
                    "id": "s1",
                    "excerpt": "Hyperliquid TVL 约 18 亿美元",
                    "page_text": "Hyperliquid TVL 约 18 亿美元",
                }
            ],
            "judge_source": "heuristic",
        }
    )
    first = grader.grade(case, observation)
    second = grader.grade(case, observation)
    assert first.scores == second.scores
    assert first.status is CaseStatus.PASS
    assert first.scores["judge_agreement"] == 1.0
    assert first.actual["hallucination_rate"] == 1.0
    assert "hallucination_rate" not in first.scores


def test_epistemic_accuracy_against_gold() -> None:
    grader = LlmJudgeGrader()
    case = EvalCase.model_validate(
        {
            "id": "c1",
            "suite": "epistemic_labeling",
            "question": "q",
            "expected": {"epistemic_type": "prediction"},
        }
    )
    result = grader.grade(
        case,
        Observation(payload={"claims": [{"id": "c1", "text": "预计下一季营收将继续增长"}]}),
    )
    assert result.status is CaseStatus.PASS
    assert result.scores["epistemic_accuracy"] == 1.0


def test_grader_fails_when_disagrees_with_gold() -> None:
    grader = LlmJudgeGrader()
    case = EvalCase.model_validate(
        {
            "id": "c1",
            "suite": "fact_check",
            "question": "q",
            "expected": {"supported": True},
        }
    )
    result = grader.grade(
        case,
        Observation(payload={"claims": [{"id": "c1", "text": "没有来源的大数字 999"}]}),
    )
    assert result.status is CaseStatus.FAIL
    assert result.scores["judge_agreement"] == 0.0


@pytest.mark.asyncio
async def test_seed_judge_suites_beat_agreement_floor(tmp_path: Path) -> None:
    report, _run_dir = await run_eval(
        EvalConfig(
            datasets_dir=DATASETS_DIR,
            results_dir=tmp_path,
            suites=("fact_check", "epistemic_labeling"),
        )
    )
    assert report.summary.failed == 0
    assert report.summary.errored == 0
    by_name = {item.name: item.value for item in report.metrics}
    assert by_name["judge_agreement"] >= AGREEMENT_MIN
    assert by_name["epistemic_accuracy"] >= 0.85
    injected, _ = await run_eval(
        EvalConfig(
            datasets_dir=DATASETS_DIR,
            results_dir=tmp_path / "injection",
            suites=("prompt_injection",),
        )
    )
    assert injected.summary.failed == 0
    assert injected.summary.errored == 0
    inj_metrics = {item.name: item.value for item in injected.metrics}
    assert inj_metrics["injection_detection_rate"] == 1.0
    again, _ = await run_eval(
        EvalConfig(
            datasets_dir=DATASETS_DIR,
            results_dir=tmp_path / "second",
            suites=("fact_check", "epistemic_labeling"),
        )
    )
    first = {item.name: item.value for item in report.metrics}
    second = {item.name: item.value for item in again.metrics}
    assert first == second


@pytest.mark.asyncio
async def test_live_judge_reads_scripted_model() -> None:
    batch = {
        "verdicts": [
            {
                "claim_id": "c1",
                "supported": True,
                "epistemic_type": "source_backed_fact",
                "rationale": "数字一致",
            }
        ]
    }
    model = ScriptedModel([[assistant_message(json.dumps(batch, ensure_ascii=False))]])
    agent = Agent[None](
        name="eval_claim_judge",
        instructions="judge",
        model=model,
        model_settings=ModelSettings(temperature=0),
        tools=[],
    )
    judge = LiveClaimJudge(agent=agent, strategy=build_strategy(JudgeBatch, _caps()))
    payload = {
        "claims": [{"id": "c1", "text": "HYPE TVL 约 18 亿美元", "source_ids": ["s1"]}],
        "sources": [{"id": "s1", "excerpt": "Hyperliquid TVL 约 18 亿美元"}],
    }
    set_live_judge(judge)
    try:
        verdicts = await judge.judge(payload)
    finally:
        set_live_judge(None)
    assert len(verdicts) == 1
    assert verdicts[0].supported is True
    assert verdicts[0].epistemic_type == "source_backed_fact"


def test_heuristic_judge_ids_follow_claims() -> None:
    payload = {
        "claims": [
            {"id": "a", "text": "比特币是一种加密资产"},
            {"id": "b", "text": "预计下一季营收将继续增长"},
        ]
    }
    verdicts = heuristic_judge(payload)
    assert [item.claim_id for item in verdicts] == ["a", "b"]
    assert verdicts[0].epistemic_type == "fact"
    assert verdicts[1].epistemic_type == "prediction"


def test_seed_fact_check_has_injected_errors() -> None:
    cases = load_jsonl(DATASETS_DIR / "fact_check.jsonl")
    assert len(cases) >= 20
    assert all(case.suite == "fact_check" for case in cases)
    injected = [case for case in cases if "injected" in case.tags]
    assert len(injected) >= 8
    assert any(case.expected.get("supported") is False for case in injected)
    assert any(case.expected.get("supported") is True for case in cases)


def test_seed_epistemic_labeling_covers_types() -> None:
    cases = load_jsonl(DATASETS_DIR / "epistemic_labeling.jsonl")
    assert len(cases) >= 20
    types = {str(case.expected.get("epistemic_type")) for case in cases}
    assert types >= {
        "source_backed_fact",
        "fact",
        "analysis",
        "inference",
        "prediction",
        "opinion",
    }


def test_seed_prompt_injection_matches_detector() -> None:
    cases = load_jsonl(DATASETS_DIR / "prompt_injection.jsonl")
    assert len(cases) >= 20
    benign = [case for case in cases if "benign" in case.tags]
    attacks = [case for case in cases if "benign" not in case.tags]
    assert benign
    assert attacks
    for case in cases:
        recorded = (case.fixtures or {}).get("observation") or {}
        text = str(recorded.get("text") or case.question)
        assert list(detect_injection(text)) == list(case.expected.get("patterns") or [])
