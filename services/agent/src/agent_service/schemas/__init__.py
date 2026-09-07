"""数据契约。

**这里是跨语言类型的唯一真源**（DEVELOPMENT_PLAN.md §5.1）。
改动任何模型后必须运行 `pnpm gen:types` 重新生成 TS 类型并提交，CI 会检查漂移。

模块划分：
    common    枚举与基类
    entities  Entity / MetricPoint
    sources   Source
    claims    ClaimDraft（LLM 输出）/ Claim（补全后）
    tools     ToolResult 契约与错误分类
    plan      ResearchTask / ResearchPlan
    findings  AgentFinding / ResearchFinding / FactCheckResult
    report    ResearchReport
    events    事件协议（判别联合）
"""

from agent_service.schemas.claims import Claim, ClaimDraft
from agent_service.schemas.common import (
    AgentName,
    AssetType,
    ConfidenceLevel,
    EpistemicType,
    ModelRole,
    QuestionType,
    Schema,
    SessionStatus,
    SourceReliability,
    SourceType,
    Stage,
    TaskStatus,
    VerificationStatus,
)
from agent_service.schemas.entities import Entity, MetricPoint
from agent_service.schemas.events import (
    TERMINAL_EVENT_TYPES,
    ErrorInfo,
    EventEnvelope,
    EventType,
    ResearchEvent,
    ResearchEventAdapter,
    TokenUsage,
)
from agent_service.schemas.findings import (
    AgentFinding,
    ClaimVerification,
    Conflict,
    FactCheckResult,
    ResearchFinding,
)
from agent_service.schemas.plan import ResearchPlan, ResearchTask
from agent_service.schemas.report import ReportMetadata, ReportSection, ResearchReport
from agent_service.schemas.sources import Source
from agent_service.schemas.tools import (
    DataProvenance,
    DataQuality,
    ToolError,
    ToolErrorCode,
    ToolResult,
)

__all__ = [
    "TERMINAL_EVENT_TYPES",
    "AgentFinding",
    "AgentName",
    "AssetType",
    "Claim",
    "ClaimDraft",
    "ClaimVerification",
    "ConfidenceLevel",
    "Conflict",
    "DataProvenance",
    "DataQuality",
    "Entity",
    "EpistemicType",
    "ErrorInfo",
    "EventEnvelope",
    "EventType",
    "FactCheckResult",
    "MetricPoint",
    "ModelRole",
    "QuestionType",
    "ReportMetadata",
    "ReportSection",
    "ResearchEvent",
    "ResearchEventAdapter",
    "ResearchFinding",
    "ResearchPlan",
    "ResearchReport",
    "ResearchTask",
    "Schema",
    "SessionStatus",
    "Source",
    "SourceReliability",
    "SourceType",
    "Stage",
    "TaskStatus",
    "TokenUsage",
    "ToolError",
    "ToolErrorCode",
    "ToolResult",
    "VerificationStatus",
]
