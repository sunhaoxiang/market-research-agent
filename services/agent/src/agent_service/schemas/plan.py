"""研究计划（§7.3）。

`ResearchPlan` 是 Research Manager 的 `output_type`，也是前端一次性画出完整任务树的依据。
因此它同时是 LLM 输出契约和 UI 契约——改动需同时考虑两侧。
"""

from __future__ import annotations

from pydantic import Field

from agent_service.schemas.common import AgentName, QuestionType, Schema
from agent_service.schemas.entities import Entity


class ResearchTask(Schema):
    id: str = Field(description="计划内唯一，建议 t1/t2/…（供 depends_on 引用）")
    agent: AgentName = Field(description="承担此任务的 Agent。枚举值，由代码校验")
    objective: str = Field(description="给子 Agent 的具体目标，需自包含")
    entities: list[Entity] = Field(default_factory=list)
    suggested_tools: list[str] = Field(
        default_factory=list, description="建议使用的工具，非强制——Agent 可自行判断"
    )
    depends_on: list[str] = Field(
        default_factory=list, description="依赖的任务 id。用于分层 fan-out，必须无环"
    )
    priority: int = 0


class ResearchPlan(Schema):
    question_type: QuestionType
    interpretation: str = Field(description="Agent 对问题的理解，展示给用户以便及早发现偏差")
    entities: list[Entity] = Field(default_factory=list)
    tasks: list[ResearchTask] = Field(default_factory=list)
    report_sections: list[str] = Field(
        default_factory=list,
        description=(
            "动态报告结构。单标的深研用完整章节；"
            '"为什么今天涨"用 Overview/Catalysts/Analysis/Risks 精简结构'
        ),
    )
    assumptions: list[str] = Field(
        default_factory=list, description="规划时做的假设，需在报告中披露"
    )
