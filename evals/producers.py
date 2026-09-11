"""Eval producer：从系统或 fixture 得到 observation。"""

from __future__ import annotations

from agent_service.orchestrator.intent import Intent, classify_by_rules
from agent_service.schemas.common import AgentName, AssetType, QuestionType
from agent_service.tools.web.untrusted import (
    UNTRUSTED_CLOSE,
    UNTRUSTED_OPEN,
    detect_injection,
    wrap_untrusted,
)
from evals.schemas import EvalCase, Observation


def specialist_agents(intent: Intent) -> list[str]:
    """路由 eval 用的专家 Agent 集合，与 `gaps._specialist_for` 对齐。"""
    types = {entity.type for entity in intent.entities}
    if intent.question_type is QuestionType.STOCK:
        return [AgentName.STOCK_RESEARCH.value]
    if intent.question_type is QuestionType.CRYPTO:
        return [AgentName.CRYPTO_RESEARCH.value]
    if intent.question_type is QuestionType.COMPARE:
        names: list[str] = []
        if AssetType.STOCK in types:
            names.append(AgentName.STOCK_RESEARCH.value)
        if AssetType.CRYPTO in types:
            names.append(AgentName.CRYPTO_RESEARCH.value)
        return names or [AgentName.WEB_RESEARCH.value]
    return [AgentName.WEB_RESEARCH.value]


class IntentRoutingProducer:
    """离线规则分类。对不上规则时与 `classify_intent` 一样落 generic。"""

    async def produce(self, case: EvalCase) -> Observation:
        fixtures = case.fixtures or {}
        recorded = fixtures.get("observation")
        if isinstance(recorded, dict):
            return Observation(payload=recorded)

        intent = classify_by_rules(case.question)
        if intent is None:
            return Observation(
                payload={
                    "question_type": QuestionType.GENERIC.value,
                    "entities": [],
                    "agents": [AgentName.WEB_RESEARCH.value],
                    "source": "none",
                }
            )
        return Observation(
            payload={
                "question_type": intent.question_type.value,
                "entities": [entity.symbol for entity in intent.entities],
                "agents": specialist_agents(intent),
                "source": intent.source,
            }
        )


class FixtureProducer:
    """P7-2/P7-5：把录制的 observation 原样送进 grader，保证分数可复现。"""

    async def produce(self, case: EvalCase) -> Observation:
        fixtures = case.fixtures or {}
        payload = fixtures.get("observation")
        if not isinstance(payload, dict):
            return Observation(error="缺少 fixtures.observation")
        return Observation(payload=payload)


def _injection_texts(recorded: dict[str, object]) -> list[str]:
    raw = recorded.get("texts")
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item)]
    text = recorded.get("text")
    if isinstance(text, str) and text:
        return [text]
    return []


class PromptInjectionProducer:
    """对录制网页跑 `detect_injection` / `wrap_untrusted`，不打网络。"""

    async def produce(self, case: EvalCase) -> Observation:
        fixtures = case.fixtures or {}
        recorded = fixtures.get("observation")
        blob: dict[str, object] = dict(recorded) if isinstance(recorded, dict) else {}
        texts = _injection_texts(blob)
        if not texts and case.question:
            texts = [case.question]
        wrapped = wrap_untrusted(texts[0]) if texts else None
        isolated = bool(
            isinstance(wrapped, str)
            and wrapped.startswith(UNTRUSTED_OPEN)
            and wrapped.endswith(UNTRUSTED_CLOSE)
        )
        inner = ""
        if isolated and wrapped is not None:
            inner = wrapped[len(UNTRUSTED_OPEN) : -len(UNTRUSTED_CLOSE)]
        return Observation(
            payload={
                "patterns": list(detect_injection(*texts)),
                "texts": texts,
                "wrapped": wrapped,
                "isolated": isolated,
                "no_breakout": isolated and UNTRUSTED_CLOSE not in inner,
            }
        )
