"""意图分类（P5-1）：常见问题走词典/正则，不打 LLM。"""

from __future__ import annotations

import asyncio
import json

import pytest
from agents.testing import ScriptedModel, assistant_message
from pydantic import SecretStr

from agent_service.models.registry import ModelRegistry
from agent_service.observability.event_bus import EventBus
from agent_service.orchestrator.intent import (
    IntentClassifier,
    build_intent_classifier,
    classify_by_rules,
    classify_intent,
)
from agent_service.schemas.common import AssetType, QuestionType
from agent_service.schemas.events import EventType, IntentClassifiedPayload
from agent_service.testing import IsolatedProviderCredentials, IsolatedSettings

_COMMON = (
    ("NVDA 是做什么的", QuestionType.STOCK, ("NVDA",)),
    ("分析 NVDA 最近一季财报", QuestionType.STOCK, ("NVDA",)),
    ("NVDA 估值贵不贵", QuestionType.STOCK, ("NVDA",)),
    ("英伟达最新一季财报的关键信号是什么？", QuestionType.STOCK, ("NVDA",)),
    ("HYPE 最近有什么重要进展？", QuestionType.CRYPTO, ("HYPE",)),
    ("介绍一下 HYPE", QuestionType.CRYPTO, ("HYPE",)),
    ("查询 HYPE 的 TVL、交易量和资金变化", QuestionType.CRYPTO, ("HYPE",)),
    ("Hyperliquid 怎么样？", QuestionType.CRYPTO, ("HYPE",)),
    ("比较 NVDA、AMD、AVGO", QuestionType.COMPARE, ("NVDA", "AMD", "AVGO")),
    ("比较 Solana 和 Sui", QuestionType.COMPARE, ("SOL", "SUI")),
    ("美联储会不会降息", QuestionType.MACRO, ()),
    ("你好", QuestionType.GENERIC, ()),
)


@pytest.mark.parametrize(("question", "expected_type", "symbols"), _COMMON)
def test_common_questions_classify_without_a_model(
    question: str, expected_type: QuestionType, symbols: tuple[str, ...]
) -> None:
    intent = classify_by_rules(question)
    assert intent is not None
    assert intent.source == "rules"
    assert intent.question_type is expected_type
    assert tuple(entity.symbol for entity in intent.entities) == symbols


def test_short_english_words_are_not_tickers() -> None:
    """`sol` / `meta` / `hype` 是英文词，不能靠大小写不敏感的 ticker 命中。"""
    assert classify_by_rules("What's the hype about?") is not None
    intent = classify_by_rules("What's the hype about?")
    assert intent is not None
    assert intent.question_type is QuestionType.GENERIC
    assert intent.entities == ()


def test_mixed_assets_without_compare_need_the_model() -> None:
    assert classify_by_rules("NVDA 和 BTC 最近谁更强") is None


def test_explicit_compare_allows_mixed_assets() -> None:
    intent = classify_by_rules("比较 NVDA 和 BTC")
    assert intent is not None
    assert intent.question_type is QuestionType.COMPARE
    assert {entity.symbol for entity in intent.entities} == {"NVDA", "BTC"}


def test_macro_plus_ticker_is_ambiguous() -> None:
    assert classify_by_rules("NVDA 会受美联储降息影响吗") is None


def test_compare_with_one_ticker_is_ambiguous() -> None:
    assert classify_by_rules("比较一下 NVDA 和它的同行") is None


def test_uppercase_tickers_still_match() -> None:
    intent = classify_by_rules("Buy HYPE or wait?")
    assert intent is not None
    assert intent.question_type is QuestionType.CRYPTO
    assert intent.entities[0].symbol == "HYPE"
    assert intent.entities[0].type is AssetType.CRYPTO


def _empty_classifier() -> IntentClassifier:
    """ScriptedModel 没有预设回复：一旦被调用就会失败。"""
    registry = ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
        )
    )
    built = build_intent_classifier(registry)
    scripted = ScriptedModel([])
    return IntentClassifier(
        agent=built.agent.clone(model=scripted),
        strategy=built.strategy,
        entry=built.entry,
    )


async def test_classify_intent_does_not_call_llm_for_common_questions() -> None:
    classifier = _empty_classifier()
    intent = await classify_intent("NVDA 是做什么的", classifier=classifier)
    assert intent.source == "rules"
    assert intent.question_type is QuestionType.STOCK


async def test_classify_intent_emits_before_returning() -> None:
    bus = EventBus("sess-1", heartbeat_interval_s=60.0)
    intent = await classify_intent("比较 NVDA、AMD、AVGO", bus=bus)
    assert intent.question_type is QuestionType.COMPARE
    bus.close()
    events = [event async for event in bus.stream()]
    assert [event.type for event in events] == [EventType.INTENT_CLASSIFIED]
    payload = events[0].payload
    assert isinstance(payload, IntentClassifiedPayload)
    assert [entity.symbol for entity in payload.entities] == ["NVDA", "AMD", "AVGO"]
    assert events[0].message == "识别为 compare · NVDA、AMD、AVGO"


def _model_classifier(reply: str) -> IntentClassifier:
    registry = ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
        )
    )
    built = build_intent_classifier(registry)
    scripted = ScriptedModel([[assistant_message(reply)]])
    return IntentClassifier(
        agent=built.agent.clone(model=scripted),
        strategy=built.strategy,
        entry=built.entry,
    )


async def test_ambiguous_questions_fall_through_to_fast_model() -> None:
    reply = json.dumps(
        {
            "question_type": "stock",
            "interpretation": "同行对比但只点了 NVDA",
            "entities": [{"type": "stock", "symbol": "NVDA", "name": "NVIDIA"}],
        },
        ensure_ascii=False,
    )
    intent = await classify_intent(
        "比较一下 NVDA 和它的同行",
        classifier=_model_classifier(reply),
    )
    assert intent.source == "model"
    assert intent.question_type is QuestionType.STOCK
    assert [entity.symbol for entity in intent.entities] == ["NVDA"]


async def test_model_failure_falls_back_to_generic() -> None:
    intent = await classify_intent(
        "比较一下 NVDA 和它的同行",
        classifier=_empty_classifier(),
    )
    assert intent.source == "fallback"
    assert intent.question_type is QuestionType.GENERIC


async def test_model_timeout_falls_back_to_generic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _hang(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(10)

    monkeypatch.setattr("agent_service.orchestrator.intent.run_structured", _hang)
    monkeypatch.setattr("agent_service.orchestrator.intent.CLASSIFY_TIMEOUT_S", 0.05)
    intent = await classify_intent(
        "比较一下 NVDA 和它的同行",
        classifier=_empty_classifier(),
    )
    assert intent.source == "fallback"
    assert intent.question_type is QuestionType.GENERIC
