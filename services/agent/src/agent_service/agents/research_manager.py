"""Research Manager —— 规划 Agent（§6.2，P1-9）。

它不使用任何工具：唯一职责是把用户问题变成 `ResearchPlan`。给规划者配工具会
诱导它"先查一下再规划"，把成本与延迟推到规划阶段，而那本该是执行阶段的事。
P5-2 把子 Agent 装进编排层（`SubAgentRunner`），**不要**用 `agent.as_tool()`
挂到本 Agent 上——那会变成 LLM 自主循环，违背决策 A。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agents import Agent, ModelSettings

from agent_service.models.structured_output import (
    StructuredOutputStrategy,
    build_strategy,
)
from agent_service.observability.prompts import PromptFingerprint, fingerprint
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
    prompt: PromptFingerprint
    """system prompt 的 hash 与长度，用于 `agent_runs` 埋点（§20.1）。

    在构造时算一次而不是每次 run 都算：prompt 前缀必须逐字节稳定才能命中缓存
    （§9.8），所以它本就是常量。反过来说，如果哪天同一个 Agent 在不同会话里
    的 hash 变了，那正是缓存失效的信号。"""

    @property
    def model_id(self) -> str:
        return self.entry.id


def build_research_manager(
    registry: ModelRegistry,
    limits: ExecutionLimits,
    *,
    model_id: str | None = None,
) -> PlannerAgent:
    """构造规划 Agent。

    缺省按角色解析（`ModelRole.PLANNER`），因此换模型只需改环境变量——
    这正是 §9.5 模型抽象层想要的效果。`model_id` 用于前端模型选择器
    显式指定本次会话用哪个模型，此时跳过角色映射。
    """
    resolved = registry.resolve(model_id) if model_id else registry.for_role(ModelRole.PLANNER)
    instructions = render_prompt(PROMPT_NAME, max_tasks=limits.max_tasks_per_plan)

    return PlannerAgent(
        agent=Agent[None](
            name=AgentName.RESEARCH_MANAGER.value,
            instructions=instructions,
            model=resolved.model,
            model_settings=_settings(resolved.settings),
            tools=[],
        ),
        strategy=build_strategy(ResearchPlan, resolved.entry.capabilities),
        entry=resolved.entry,
        # 指纹只覆盖 system prompt 本体，不含 json_mode 追加的 schema 说明：
        # 后者由 output schema 唯一决定，混进来会让"prompt 改没改"这个问题
        # 掺入"schema 改没改"，而两者的排查方向完全不同
        prompt=fingerprint(instructions),
    )


def _settings(base: ModelSettings) -> ModelSettings:
    """规划阶段压低随机性。

    同一个问题应该得到基本一致的计划：用户重跑一次却看到完全不同的任务树，
    会让人怀疑系统的可靠性；Phase 7 的 eval 也需要可比的基线。
    """
    return base.resolve(ModelSettings(temperature=0.2))
