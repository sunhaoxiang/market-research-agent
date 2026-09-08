"""Web Research Agent（§6.2，P2-6）。

LLM 只输出 `AgentFinding`（含 s1/s2 短引用）。真正的 Source / Claim.source_ids
由 tool 层的 `SourceCollector` 补全——让模型复述 URL 是幻觉高发点（§15.1）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agents import Agent, ModelSettings

from agent_service.models.structured_output import build_strategy
from agent_service.observability.prompts import PromptFingerprint, fingerprint
from agent_service.prompts import load_prompt
from agent_service.schemas.claims import Claim
from agent_service.schemas.common import AgentName, EpistemicType, ModelRole
from agent_service.schemas.findings import AgentFinding, ResearchFinding
from agent_service.tools.web.bindings import WEB_TOOLS

if TYPE_CHECKING:
    from agent_service.models.catalog import ModelEntry
    from agent_service.models.registry import ModelRegistry
    from agent_service.models.structured_output import StructuredOutputStrategy
    from agent_service.schemas.plan import ResearchTask
    from agent_service.tools.web.collector import SourceCollector

PROMPT_NAME = "web_research"
_UNTRUSTED_PROMPT = "untrusted_web"


@dataclass(frozen=True)
class WebResearchAgent:
    agent: Agent[Any]
    strategy: StructuredOutputStrategy[AgentFinding]
    entry: ModelEntry
    prompt: PromptFingerprint

    @property
    def model_id(self) -> str:
        return self.entry.id


def build_web_research(registry: ModelRegistry, *, model_id: str | None = None) -> WebResearchAgent:
    resolved = registry.resolve(model_id) if model_id else registry.for_role(ModelRole.FAST)
    instructions = load_prompt(_UNTRUSTED_PROMPT) + "\n\n" + load_prompt(PROMPT_NAME)
    return WebResearchAgent(
        agent=Agent(
            name=AgentName.WEB_RESEARCH.value,
            instructions=instructions,
            model=resolved.model,
            model_settings=resolved.settings.resolve(ModelSettings(temperature=0.3)),
            tools=WEB_TOOLS,
        ),
        strategy=build_strategy(AgentFinding, resolved.entry.capabilities),
        entry=resolved.entry,
        prompt=fingerprint(instructions),
    )


def assemble_finding(
    task: ResearchTask,
    draft: AgentFinding,
    collector: SourceCollector,
) -> ResearchFinding:
    """把 LLM 草稿和 tool 登记的来源拼成 ResearchFinding。"""
    sources = collector.sources()
    by_ref = {source.ref: source for source in sources}
    claims: list[Claim] = []
    gaps = list(draft.data_gaps)
    unknown_refs: list[str] = []

    for item in draft.claims:
        resolved_ids: list[str] = []
        for ref in item.source_refs:
            source = by_ref.get(ref)
            if source is None:
                unknown_refs.append(ref)
                continue
            resolved_ids.append(source.id)
        epistemic = item.epistemic_type
        if epistemic is EpistemicType.SOURCE_BACKED_FACT and not resolved_ids:
            epistemic = EpistemicType.FACT
            gaps.append(f"陈述缺少可解析来源，已降级为 fact：{item.text}")
        claims.append(
            Claim(
                text=item.text,
                epistemic_type=epistemic,
                confidence=item.confidence,
                source_ids=resolved_ids,
                as_of=item.as_of,
                task_id=task.id,
                agent=task.agent.value,
            )
        )

    if unknown_refs:
        unique = ", ".join(dict.fromkeys(unknown_refs))
        gaps.append(f"claim 引用了工具结果中不存在的来源：{unique}")
    if not sources:
        gaps.append("本次任务未获得任何网页来源")

    return ResearchFinding(
        task_id=task.id,
        agent=task.agent,
        summary=draft.summary,
        claims=claims,
        sources=sources,
        metrics=draft.metrics,
        data_gaps=gaps,
        tool_errors=list(collector.errors),
    )


def web_research_user_message(
    task: ResearchTask,
    *,
    now: datetime | None = None,
    upstream_summaries: tuple[str, ...] = (),
    missing_upstream: tuple[str, ...] = (),
) -> str:
    """任务说明放在 user 消息，避免日期/objective 污染 system prompt 缓存前缀。"""
    moment = now or datetime.now(UTC)
    parts = [
        f"当前日期：{moment.strftime('%Y-%m-%d')}（UTC）",
        f"任务目标：{task.objective.strip()}",
    ]
    if task.entities:
        labels = ", ".join(entity.name or entity.symbol for entity in task.entities)
        parts.append(f"相关实体：{labels}")
    if task.suggested_tools:
        parts.append("建议工具：" + ", ".join(task.suggested_tools))
    if upstream_summaries:
        joined = "\n".join(f"- {text}" for text in upstream_summaries)
        parts.append(f"上游任务已发现：\n{joined}")
    if missing_upstream:
        parts.append(
            "注意：依赖任务 "
            + ", ".join(missing_upstream)
            + " 失败，请在 data_gaps 中披露因此缺了什么。"
        )
    parts.append("请检索公开网页并输出结构化发现。")
    return "\n\n".join(parts)
