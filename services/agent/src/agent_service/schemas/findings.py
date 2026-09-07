"""子 Agent 的研究结果与事实核查结果（§6.2）。

`AgentFinding` 与 `ResearchFinding` 的分离是有意的：
LLM 只输出前者（不含 sources），编排层用工具调用记录补全后者。
理由见 sources.py 的模块说明——让 LLM 复述 URL 是已知的幻觉高发点。
"""

from __future__ import annotations

from pydantic import Field

from agent_service.schemas.claims import Claim, ClaimDraft
from agent_service.schemas.common import (
    AgentName,
    ConfidenceLevel,
    Schema,
    VerificationStatus,
)
from agent_service.schemas.entities import MetricPoint
from agent_service.schemas.sources import Source
from agent_service.schemas.tools import ToolError


class AgentFinding(Schema):
    """子 Agent 的 LLM 输出（`output_type`）。"""

    summary: str = Field(description="本任务发现的要点，2-4 句")
    claims: list[ClaimDraft] = Field(default_factory=list)
    metrics: list[MetricPoint] = Field(default_factory=list)
    data_gaps: list[str] = Field(
        default_factory=list,
        description=(
            "明确声明拿不到什么数据。这是反幻觉的关键设计——强制显式声明缺失，而不是用推测填补空白"
        ),
    )


class ResearchFinding(Schema):
    """编排层组装后的完整结果，进入 Report Writer 的输入。"""

    task_id: str
    agent: AgentName
    summary: str
    claims: list[Claim] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    metrics: list[MetricPoint] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    tool_errors: list[ToolError] = Field(default_factory=list)


class ClaimVerification(Schema):
    """Fact Checker 对单条 claim 的裁定。"""

    claim_id: str
    verification: VerificationStatus
    note: str | None = Field(default=None, description="裁定理由，冲突时需说明取舍依据")
    confidence_adjustment: ConfidenceLevel | None = Field(
        default=None, description="若核查后置信度需调整，给出新值"
    )
    additional_source_refs: list[str] = Field(
        default_factory=list, description="核查过程中新增的支撑来源"
    )


class Conflict(Schema):
    """多来源冲突（§12.2 CONFLICT_DETECTED）。"""

    claim_ids: list[str]
    description: str
    values: list[str] = Field(
        default_factory=list, description="各来源给出的不同值，用于 UI 并列展示"
    )


class FactCheckResult(Schema):
    """Fact Checker 的 `output_type`。"""

    verifications: list[ClaimVerification] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
