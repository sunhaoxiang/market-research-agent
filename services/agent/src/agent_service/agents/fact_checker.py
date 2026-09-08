"""Fact Checker Agent（§6.2，P5-5）。

干净上下文：只看 `Claim[]` + `Source[]`，不看产出 claim 时的推理、finding
摘要或用户原问题。工具仅 `web_search` / `web_fetch`（复核用），输出
`FactCheckResult`。调用方是 pipeline，不是计划任务。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agents import Agent, ModelSettings

from agent_service.models.structured_output import build_strategy
from agent_service.observability.prompts import PromptFingerprint, fingerprint
from agent_service.prompts import load_prompt
from agent_service.schemas.common import AgentName, ModelRole
from agent_service.schemas.findings import FactCheckResult
from agent_service.tools.web.bindings import FACT_CHECKER_TOOLS

__all__ = [
    "PROMPT_NAME",
    "FactCheckerAgent",
    "build_fact_checker",
    "fact_checker_user_message",
]

if TYPE_CHECKING:
    from agent_service.models.catalog import ModelEntry
    from agent_service.models.registry import ModelRegistry
    from agent_service.models.structured_output import StructuredOutputStrategy
    from agent_service.schemas.claims import Claim
    from agent_service.schemas.sources import Source

PROMPT_NAME = "fact_checker"
_UNTRUSTED_PROMPT = "untrusted_web"


@dataclass(frozen=True)
class FactCheckerAgent:
    agent: Agent[Any]
    strategy: StructuredOutputStrategy[FactCheckResult]
    entry: ModelEntry
    prompt: PromptFingerprint

    @property
    def model_id(self) -> str:
        return self.entry.id


def build_fact_checker(registry: ModelRegistry, *, model_id: str | None = None) -> FactCheckerAgent:
    resolved = registry.resolve(model_id) if model_id else registry.for_role(ModelRole.BALANCED)
    instructions = load_prompt(_UNTRUSTED_PROMPT) + "\n\n" + load_prompt(PROMPT_NAME)
    return FactCheckerAgent(
        agent=Agent(
            name=AgentName.FACT_CHECKER.value,
            instructions=instructions,
            model=resolved.model,
            model_settings=resolved.settings.resolve(ModelSettings(temperature=0.3)),
            tools=FACT_CHECKER_TOOLS,
        ),
        strategy=build_strategy(FactCheckResult, resolved.entry.capabilities),
        entry=resolved.entry,
        prompt=fingerprint(instructions),
    )


def fact_checker_user_message(
    claims: Sequence[Claim],
    sources: Sequence[Source],
    *,
    now: datetime | None = None,
) -> str:
    """日期、陈述、来源都放 user 消息，避免污染 system prompt 缓存前缀。

    故意不放用户原问题或任务摘要：Fact Checker 必须是干净上下文（§6.1）。
    """
    moment = now or datetime.now(UTC)
    by_id = {item.id: item for item in sources}
    parts = [
        f"当前日期：{moment.strftime('%Y-%m-%d')}（UTC）",
        _claims_block(claims, by_id),
        _sources_block(sources),
        "请核对以上陈述并输出结构化裁定。不要扩写研究，不要发明 claim_id。",
    ]
    return "\n\n".join(parts)


def _claims_block(claims: Sequence[Claim], by_id: dict[str, Source]) -> str:
    if not claims:
        return "待核陈述：无。"
    lines = ["待核陈述："]
    for claim in claims:
        refs = [by_id[sid].ref for sid in claim.source_ids if sid in by_id and by_id[sid].ref]
        cited = f"来源 {', '.join(refs)}" if refs else "无来源引用"
        lines.append(
            f"- id={claim.id} | {claim.epistemic_type.value} | {claim.confidence.value} | {cited}"
        )
        lines.append(f"  {claim.text}")
    return "\n".join(lines)


def _sources_block(sources: Sequence[Source]) -> str:
    if not sources:
        return "来源账本：无。"
    lines = ["来源账本："]
    for source in sources:
        title = source.title or source.domain or source.url
        lines.append(f"- {source.ref} [{source.reliability.value}] {title} — {source.url}")
        if source.excerpt:
            lines.append(f"  摘录：{source.excerpt}")
    return "\n".join(lines)
