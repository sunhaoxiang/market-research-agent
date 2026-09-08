"""Stock Research Agent（§6.2，P4-10）。

整合 stocks / financials / sec / system / web 工具。LLM 只输出 `AgentFinding`；
Source 与 claim.source_ids 由 `SourceCollector` 补全。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agents import Agent, ModelSettings, Tool

from agent_service.models.structured_output import build_strategy
from agent_service.observability.prompts import PromptFingerprint, fingerprint
from agent_service.prompts import load_prompt
from agent_service.schemas.common import AgentName, ModelRole
from agent_service.schemas.findings import AgentFinding
from agent_service.tools.financials.bindings import FINANCIALS_TOOLS
from agent_service.tools.sec.bindings import SEC_TOOLS
from agent_service.tools.stocks.bindings import STOCK_TOOLS
from agent_service.tools.system.bindings import SYSTEM_TOOLS
from agent_service.tools.web.bindings import WEB_TOOLS

if TYPE_CHECKING:
    from agent_service.models.catalog import ModelEntry
    from agent_service.models.registry import ModelRegistry
    from agent_service.models.structured_output import StructuredOutputStrategy
    from agent_service.schemas.plan import ResearchTask

PROMPT_NAME = "stock_research"
_UNTRUSTED_PROMPT = "untrusted_web"

STOCK_RESEARCH_TOOLS: list[Tool] = [
    *STOCK_TOOLS,
    *FINANCIALS_TOOLS,
    *SEC_TOOLS,
    *SYSTEM_TOOLS,
    *WEB_TOOLS,
]


@dataclass(frozen=True)
class StockResearchAgent:
    agent: Agent[Any]
    strategy: StructuredOutputStrategy[AgentFinding]
    entry: ModelEntry
    prompt: PromptFingerprint

    @property
    def model_id(self) -> str:
        return self.entry.id


def build_stock_research(
    registry: ModelRegistry, *, model_id: str | None = None
) -> StockResearchAgent:
    resolved = registry.resolve(model_id) if model_id else registry.for_role(ModelRole.BALANCED)
    instructions = load_prompt(_UNTRUSTED_PROMPT) + "\n\n" + load_prompt(PROMPT_NAME)
    return StockResearchAgent(
        agent=Agent(
            name=AgentName.STOCK_RESEARCH.value,
            instructions=instructions,
            model=resolved.model,
            model_settings=resolved.settings.resolve(ModelSettings(temperature=0.3)),
            tools=STOCK_RESEARCH_TOOLS,
        ),
        strategy=build_strategy(AgentFinding, resolved.entry.capabilities),
        entry=resolved.entry,
        prompt=fingerprint(instructions),
    )


def stock_research_user_message(
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
    parts.append("请用结构化数据工具（必要时辅以网页检索）输出结构化发现。")
    return "\n\n".join(parts)
