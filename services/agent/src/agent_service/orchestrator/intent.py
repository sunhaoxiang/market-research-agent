"""意图分类（P5-1 / [DP §25.1]）。

规划仍要跑 LLM；分类本身有 2s 预算。常见问题用词典/正则短路，完全跳过模型。
对不上再走 FAST；FAST 失败也只降成 `generic`，不让整次研究失败——没有分类
一样可以规划，只是前端「理解问题」会晚一点或更含糊。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import structlog
from agents import Agent, ModelSettings
from pydantic import Field

from agent_service.models.structured_output import (
    StructuredOutputError,
    StructuredOutputStrategy,
    build_strategy,
    run_structured,
)
from agent_service.prompts import load_prompt
from agent_service.schemas.common import AssetType, ModelRole, QuestionType, Schema
from agent_service.schemas.entities import Entity
from agent_service.schemas.events import IntentClassifiedEvent, IntentClassifiedPayload

if TYPE_CHECKING:
    from agent_service.models.catalog import ModelEntry
    from agent_service.models.registry import ModelRegistry
    from agent_service.observability.event_bus import EventBus

log = structlog.get_logger(__name__)

PROMPT_NAME = "intent_classifier"
CLASSIFY_TIMEOUT_S = 2.0
"""[DP §25.1] 意图分类预算。规则短路不计时；FAST 超时则降成 generic。"""
_MIN_COMPARE_ENTITIES = 2

IntentSource = Literal["rules", "model", "fallback"]

_COMPARE_RE = re.compile(
    r"比较|对比|相比|versus|\bvs\.?\b",
    re.IGNORECASE,
)
_MACRO_RE = re.compile(
    r"美联储|\bFed\b|\bFOMC\b|\bCPI\b|\bPCE\b|\bNFP\b|\bGDP\b|\bDXY\b|\bVIX\b"
    r"|非农|通胀|降息|加息|利率决议|国债收益率|流动性",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _Alias:
    needle: str
    """问题里出现的写法。ticker 必须与原文大小写一致（见 `_ticker_in`）。"""
    symbol: str
    name: str | None
    asset: AssetType
    kind: Literal["ticker", "name"]


def _tickers(asset: AssetType, symbol_to_name: dict[str, str | None]) -> tuple[_Alias, ...]:
    return tuple(
        _Alias(needle=symbol, symbol=symbol, name=name, asset=asset, kind="ticker")
        for symbol, name in symbol_to_name.items()
    )


def _names(
    asset: AssetType, alias_to_symbol: dict[str, tuple[str, str | None]]
) -> tuple[_Alias, ...]:
    return tuple(
        _Alias(needle=alias, symbol=symbol, name=name, asset=asset, kind="name")
        for alias, (symbol, name) in alias_to_symbol.items()
    )


# ticker 只认原文里的大写独立词，避免 sol/meta/hype 这种英文词误伤。
_STOCK_TICKERS = _tickers(
    AssetType.STOCK,
    {
        "NVDA": "NVIDIA",
        "AMD": "AMD",
        "AVGO": "Broadcom",
        "AAPL": "Apple",
        "MSFT": "Microsoft",
        "GOOG": "Alphabet",
        "GOOGL": "Alphabet",
        "AMZN": "Amazon",
        "META": "Meta",
        "TSLA": "Tesla",
        "TSM": "TSMC",
        "INTC": "Intel",
        "QCOM": "Qualcomm",
        "MU": "Micron",
        "ASML": "ASML",
        "ARM": "Arm",
        "SMCI": "Super Micro",
        "PLTR": "Palantir",
        "BRK.B": "Berkshire Hathaway",
        "BRK-B": "Berkshire Hathaway",
        "NFLX": "Netflix",
        "ORCL": "Oracle",
        "BABA": "Alibaba",
    },
)
_CRYPTO_TICKERS = _tickers(
    AssetType.CRYPTO,
    {
        "BTC": "Bitcoin",
        "ETH": "Ethereum",
        "SOL": "Solana",
        "HYPE": "Hyperliquid",
        "BNB": "BNB",
        "XRP": "XRP",
        "DOGE": "Dogecoin",
        "ADA": "Cardano",
        "SUI": "Sui",
        "AVAX": "Avalanche",
        "DOT": "Polkadot",
        "LINK": "Chainlink",
    },
)
_STOCK_NAMES = _names(
    AssetType.STOCK,
    {
        "英伟达": ("NVDA", "NVIDIA"),
        "nvidia": ("NVDA", "NVIDIA"),
        "苹果公司": ("AAPL", "Apple"),
        "特斯拉": ("TSLA", "Tesla"),
        "tesla": ("TSLA", "Tesla"),
        "微软": ("MSFT", "Microsoft"),
        "microsoft": ("MSFT", "Microsoft"),
        "台积电": ("TSM", "TSMC"),
        "tsmc": ("TSM", "TSMC"),
        "博通": ("AVGO", "Broadcom"),
        "broadcom": ("AVGO", "Broadcom"),
        "超微半导体": ("AMD", "AMD"),
    },
)
_CRYPTO_NAMES = _names(
    AssetType.CRYPTO,
    {
        "hyperliquid": ("HYPE", "Hyperliquid"),
        "比特币": ("BTC", "Bitcoin"),
        "bitcoin": ("BTC", "Bitcoin"),
        "以太坊": ("ETH", "Ethereum"),
        "ethereum": ("ETH", "Ethereum"),
        "solana": ("SOL", "Solana"),
        "sui": ("SUI", "Sui"),
    },
)
_ALIASES: tuple[_Alias, ...] = (
    *_STOCK_TICKERS,
    *_CRYPTO_TICKERS,
    *_STOCK_NAMES,
    *_CRYPTO_NAMES,
)


@dataclass(frozen=True)
class Intent:
    """一次分类结果。`source=rules` 表示没打模型。"""

    question_type: QuestionType
    entities: tuple[Entity, ...]
    interpretation: str
    source: IntentSource

    def planner_hint(self) -> str:
        symbols = "、".join(entity.symbol for entity in self.entities) or "（未抽出标的）"
        return (
            f"已识别意图：{self.question_type.value}，标的 {symbols}。"
            "规划时请与此保持一致，任务拆解仍以用户问题为准。"
        )


class IntentDraft(Schema):
    """FAST 模型的输出契约。不进跨语言事件协议。"""

    question_type: QuestionType
    interpretation: str
    entities: list[Entity] = Field(default_factory=list)


@dataclass(frozen=True)
class IntentClassifier:
    """FAST 模型兜底。不是第六个专职 Agent 之外的新角色，只是分类器用的薄封装。"""

    agent: Agent[None]
    strategy: StructuredOutputStrategy[IntentDraft]
    entry: ModelEntry


def build_intent_classifier(registry: ModelRegistry) -> IntentClassifier:
    """按 `ModelRole.FAST` 构造分类器。与用户选的会话模型无关。"""
    resolved = registry.for_role(ModelRole.FAST)
    instructions = load_prompt(PROMPT_NAME)
    return IntentClassifier(
        agent=Agent[None](
            name="intent_classifier",
            instructions=instructions,
            model=resolved.model,
            model_settings=resolved.settings.resolve(ModelSettings(temperature=0.0)),
            tools=[],
        ),
        strategy=build_strategy(IntentDraft, resolved.entry.capabilities),
        entry=resolved.entry,
    )


def classify_by_rules(question: str) -> Intent | None:
    """高置信才返回。对不上返回 None，让调用方走 FAST。"""
    text = question.strip()
    if not text:
        return None

    entities = _find_entities(text)
    types = {entity.type for entity in entities}
    mixed = AssetType.STOCK in types and AssetType.CRYPTO in types
    compare_kw = _COMPARE_RE.search(text) is not None
    macro = _MACRO_RE.search(text) is not None

    # 跨资产且没有比较词、宏观词叠 ticker：不能默认当成横比，交给 FAST。
    if (mixed and not compare_kw) or (macro and entities):
        return None

    if len(entities) >= _MIN_COMPARE_ENTITIES:
        return _intent(QuestionType.COMPARE, entities, source="rules")

    packed: tuple[Entity, ...] = entities
    kind: QuestionType | None
    if compare_kw:
        kind = None
    elif len(entities) == 1:
        only = entities[0]
        kind = QuestionType.STOCK if only.type is AssetType.STOCK else QuestionType.CRYPTO
    elif macro:
        kind = QuestionType.MACRO
        packed = ()
    elif not entities:
        # 问候语之类：短路成 generic，避免为「你好」打 FAST。
        kind = QuestionType.GENERIC
    else:
        kind = None

    return None if kind is None else _intent(kind, packed, source="rules")


async def classify_intent(
    question: str,
    *,
    classifier: IntentClassifier | None = None,
    bus: EventBus | None = None,
) -> Intent:
    """分类并（可选）立刻发出 `intent_classified`，好让前端先点亮「理解问题」。"""
    intent = classify_by_rules(question)
    if intent is None and classifier is not None:
        intent = await _classify_with_model(question, classifier)
    if intent is None:
        intent = _intent(QuestionType.GENERIC, (), source="fallback")
        log.info("intent.fallback_generic", reason="rules_and_model_missed")

    log.info(
        "intent.classified",
        question_type=intent.question_type.value,
        source=intent.source,
        symbols=[entity.symbol for entity in intent.entities],
    )
    if bus is not None:
        _emit(bus, intent)
    return intent


def _emit(bus: EventBus, intent: Intent) -> None:
    symbols = "、".join(entity.symbol for entity in intent.entities)
    suffix = f" · {symbols}" if symbols else ""
    bus.emit(
        IntentClassifiedEvent,
        payload=IntentClassifiedPayload(
            question_type=intent.question_type,
            entities=list(intent.entities),
        ),
        message=f"识别为 {intent.question_type.value}{suffix}",
    )


async def _classify_with_model(question: str, classifier: IntentClassifier) -> Intent | None:
    try:
        async with asyncio.timeout(CLASSIFY_TIMEOUT_S):
            structured = await run_structured(
                classifier.agent,
                f"用户问题：\n{question.strip()}\n\n请给出意图分类。",
                strategy=classifier.strategy,
            )
    except TimeoutError:
        log.warning("intent.model_timeout", budget_s=CLASSIFY_TIMEOUT_S)
        return None
    except StructuredOutputError:
        log.warning("intent.model_failed", reason="structured_output")
        return None
    except Exception:
        log.exception("intent.model_failed")
        return None
    draft = structured.output
    return Intent(
        question_type=draft.question_type,
        entities=tuple(draft.entities),
        interpretation=draft.interpretation,
        source="model",
    )


def _intent(
    question_type: QuestionType,
    entities: tuple[Entity, ...] | list[Entity],
    *,
    source: IntentSource,
) -> Intent:
    packed = tuple(entities)
    symbols = "、".join(entity.symbol for entity in packed)
    if question_type is QuestionType.COMPARE:
        interpretation = f"识别为横向对比，标的 {symbols}" if symbols else "识别为横向对比"
    elif question_type is QuestionType.MACRO:
        interpretation = "识别为宏观问题"
    elif question_type is QuestionType.GENERIC:
        interpretation = "未匹配到明确的加密/美股/宏观标的"
    elif symbols:
        label = "美股" if question_type is QuestionType.STOCK else "加密资产"
        interpretation = f"识别为{label}研究，标的 {symbols}"
    else:
        interpretation = f"识别为 {question_type.value}"
    return Intent(
        question_type=question_type,
        entities=packed,
        interpretation=interpretation,
        source=source,
    )


def _find_entities(question: str) -> tuple[Entity, ...]:
    """按在问题里出现的位置排序，同一 symbol 只保留一次。

    先扫长别名，避免短 ticker 误伤（例如以后加 `BRK` 时不要抢 `BRK.B`）。
    最终顺序跟用户写法走，这样「比较 NVDA、AMD、AVGO」不会变成 NVDA/AVGO/AMD。
    """
    hits: list[tuple[int, Entity]] = []
    seen: set[str] = set()
    for alias in sorted(_ALIASES, key=lambda item: len(item.needle), reverse=True):
        if alias.symbol in seen:
            continue
        index = _alias_index(question, alias)
        if index is None:
            continue
        seen.add(alias.symbol)
        hits.append((index, Entity(type=alias.asset, symbol=alias.symbol, name=alias.name)))
    hits.sort(key=lambda item: item[0])
    return tuple(entity for _, entity in hits)


def _alias_index(question: str, alias: _Alias) -> int | None:
    needle = alias.needle
    flags = 0 if alias.kind == "ticker" else re.IGNORECASE
    if alias.kind == "ticker" or needle.isascii():
        pattern = rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])"
        match = re.search(pattern, question, flags)
        return None if match is None else match.start()
    index = question.find(needle)
    return None if index < 0 else index
