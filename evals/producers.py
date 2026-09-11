"""Eval producer：从系统或 fixture 得到 observation。"""

from __future__ import annotations

from agent_service.orchestrator.intent import Intent, classify_by_rules
from agent_service.schemas.common import AgentName, AssetType, QuestionType
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
