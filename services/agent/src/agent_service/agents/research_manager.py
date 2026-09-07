"""Research Manager —— 规划 Agent（§6.2，P1-9）。

它不使用任何工具：唯一职责是把用户问题变成 `ResearchPlan`。给规划者配工具会
诱导它"先查一下再规划"，把成本与延迟推到规划阶段，而那本该是执行阶段的事。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agents import Agent, ModelSettings

from agent_service.models.structured_output import (
    StructuredOutputStrategy,
    build_strategy,
)
from agent_service.prompts import render_prompt
from agent_service.schemas.common import AgentName, ModelRole
from agent_service.schemas.plan import ResearchPlan

if TYPE_CHECKING:
    from agent_service.config import ExecutionLimits
    from agent_service.models.catalog import ModelEntry
    from agent_service.models.registry import ModelRegistry

PROMPT_NAME = "research_manager"


@dataclass(frozen=True)
class PlannerAgent:
    """规划 Agent 及其运行所需的配套信息。"""

    agent: Agent[None]
    strategy: StructuredOutputStrategy[ResearchPlan]
    """策略不塞进 Agent：「要不要手动解析、失败怎么重试」是调用方的职责，
    不是 Agent 的属性。"""
    entry: ModelEntry
    """目录条目。带着它而不只是 id，是因为成本核算需要 `capabilities.pricing`，
    而 `Agent` 上只有构造好的 `Model` 实例，拿不回定价元数据。"""

    @property
    def model_id(self) -> str:
        return self.entry.id


def build_research_manager(registry: ModelRegistry, limits: ExecutionLimits) -> PlannerAgent:
    """构造规划 Agent。

    模型按角色解析（`ModelRole.PLANNER`），因此换模型只需改环境变量——
    这正是 §9.5 模型抽象层想要的效果。
    """
    resolved = registry.for_role(ModelRole.PLANNER)

    return PlannerAgent(
        agent=Agent[None](
            name=AgentName.RESEARCH_MANAGER.value,
            instructions=render_prompt(PROMPT_NAME, max_tasks=limits.max_tasks_per_plan),
            model=resolved.model,
            model_settings=_settings(resolved.settings),
            tools=[],
        ),
        strategy=build_strategy(ResearchPlan, resolved.entry.capabilities),
        entry=resolved.entry,
    )


def _settings(base: ModelSettings) -> ModelSettings:
    """规划阶段压低随机性。

    同一个问题应该得到基本一致的计划：用户重跑一次却看到完全不同的任务树，
    会让人怀疑系统的可靠性；Phase 7 的 eval 也需要可比的基线。
    """
    return base.resolve(ModelSettings(temperature=0.2))
