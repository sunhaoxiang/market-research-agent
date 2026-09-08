"""事件协议（§12）。

四条设计原则的落地方式：

1. **Discriminated union**：`type` 为判别字段。每个事件是独立的类而非通用信封 +
   无类型 payload——这样生成的 TS 类型能让前端 reducer 在 `switch (event.type)`
   中自动收窄 `event.payload`，编译期就能发现字段错误。
2. **每个事件自带 `seq`**（会话内单调递增），支持断线重连与顺序保证。
3. **事件是"状态变更通知"而非日志字符串**：payload 结构化，前端据此维护状态树。
4. `message` 是有意的冗余：前端不必为每种事件写文案逻辑，未覆盖的类型也能优雅显示。

Agents SDK 的 `RunItemStreamEvent` 等在编排层**翻译**为本协议事件，不直接透传,
避免前端耦合 SDK 内部结构（§12.1-5）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from agent_service.schemas.claims import Claim
from agent_service.schemas.common import (
    AgentName,
    QuestionType,
    Schema,
    Stage,
    TaskStatus,
    VerificationStatus,
)
from agent_service.schemas.entities import Entity, MetricPoint
from agent_service.schemas.findings import Conflict
from agent_service.schemas.plan import ResearchPlan, ResearchTask
from agent_service.schemas.report import ResearchReport
from agent_service.schemas.sources import Source
from agent_service.schemas.tools import ToolErrorCode


class EventType(StrEnum):
    # ── 会话级
    SESSION_STARTED = "session_started"
    SESSION_COMPLETED = "session_completed"
    SESSION_FAILED = "session_failed"
    SESSION_CANCELLED = "session_cancelled"
    # ── 规划
    INTENT_CLASSIFIED = "intent_classified"
    PLAN_CREATED = "plan_created"
    PLAN_UPDATED = "plan_updated"
    # ── 阶段
    STAGE_CHANGED = "stage_changed"
    # ── Agent
    AGENT_STARTED = "agent_started"
    AGENT_PROGRESS = "agent_progress"
    AGENT_REASONING = "agent_reasoning"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    AGENT_HANDOFF = "agent_handoff"
    # ── Tool
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    # ── 数据/来源
    SOURCE_FOUND = "source_found"
    METRIC_FOUND = "metric_found"
    # ── 事实核查
    FACT_CHECK_STARTED = "fact_check_started"
    FACT_CHECK_PROGRESS = "fact_check_progress"
    CLAIM_VERIFIED = "claim_verified"
    CONFLICT_DETECTED = "conflict_detected"
    # ── 报告
    REPORT_STARTED = "report_started"
    REPORT_SECTION_DELTA = "report_section_delta"
    REPORT_COMPLETED = "report_completed"
    # ── 其他
    USAGE_UPDATED = "usage_updated"
    AGENT_RUN_METRICS = "agent_run_metrics"
    WARNING = "warning"
    HEARTBEAT = "heartbeat"


class ErrorInfo(Schema):
    code: str
    message: str


class TokenUsage(Schema):
    input: int = 0
    output: int = 0
    cached: int = 0
    """命中 prompt 缓存的输入 token。缓存命中价仅为未命中的 3%，必须单独统计（§9.8）。"""

    def __add__(self, other: TokenUsage) -> TokenUsage:
        """合并两次调用的用量。

        一次 Agent run 常包含多次模型调用（结构化输出的修正重试、Phase 2 起的
        工具循环），而 SDK 的 usage 是**每次调用**独立的。没有这个运算就只能
        手抄三个字段相加，抄漏一处的后果是成本被静默低估。
        """
        return TokenUsage(
            input=self.input + other.input,
            output=self.output + other.output,
            cached=self.cached + other.cached,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Payloads
# ─────────────────────────────────────────────────────────────────────────────


class SessionStartedPayload(Schema):
    question: str
    model_id: str


class SessionCompletedPayload(Schema):
    duration_ms: int
    usage: TokenUsage
    cost_usd: float | None = None


class SessionFailedPayload(Schema):
    error: ErrorInfo
    stage: Stage | None = None


class IntentClassifiedPayload(Schema):
    question_type: QuestionType
    entities: list[Entity] = Field(default_factory=list)


class PlanCreatedPayload(Schema):
    plan: ResearchPlan
    """前端据此一次性画出完整任务树（§12.2）。"""


class PlanUpdatedPayload(Schema):
    added_tasks: list[ResearchTask] = Field(default_factory=list)
    reason: str | None = None


class StageChangedPayload(Schema):
    stage: Stage
    previous: Stage | None = None


class AgentStartedPayload(Schema):
    agent: AgentName
    task_id: str
    objective: str
    model_id: str


class AgentProgressPayload(Schema):
    agent: AgentName
    task_id: str
    message: str
    """人类可读的当前动作。"""


class AgentReasoningPayload(Schema):
    agent: AgentName
    task_id: str | None = None
    summary: str
    """仅 reasoning 模型会产生；是摘要而非完整思维链。"""


class AgentCompletedPayload(Schema):
    agent: AgentName
    task_id: str
    summary: str
    claim_count: int = 0
    source_count: int = 0
    duration_ms: int


class AgentFailedPayload(Schema):
    agent: AgentName
    task_id: str
    error: ErrorInfo


class AgentHandoffPayload(Schema):
    from_agent: AgentName
    to_agent: AgentName
    reason: str | None = None


class ToolStartedPayload(Schema):
    call_id: str
    tool: str
    agent: AgentName
    task_id: str | None = None
    input_summary: str | None = Field(default=None, description="摘要而非完整入参，避免事件流膨胀")


class ToolCompletedPayload(Schema):
    call_id: str
    tool: str
    ok: bool
    provider: str | None = None
    cache_hit: bool = False
    duration_ms: int
    result_summary: str | None = None


class ToolFailedPayload(Schema):
    call_id: str
    tool: str
    error_code: ToolErrorCode
    message: str


class SourceFoundPayload(Schema):
    source: Source
    """Source Panel 据此实时增长。"""


class MetricFoundPayload(Schema):
    metric: MetricPoint


class FactCheckStartedPayload(Schema):
    claim_count: int


class FactCheckProgressPayload(Schema):
    checked: int
    total: int


class ClaimVerifiedPayload(Schema):
    claim_id: str
    verification: VerificationStatus
    note: str | None = None


class ConflictDetectedPayload(Schema):
    conflict: Conflict


class ReportSectionDeltaPayload(Schema):
    section_id: str
    text: str
    """增量文本，前端追加而非替换。"""


class ReportCompletedPayload(Schema):
    report: ResearchReport
    sources: list[Source] = Field(default_factory=list)
    """已编 citation_index 的参考文献，供 Source Panel 把 [n] 对上来源。"""
    claims: list[Claim] = Field(
        default_factory=list,
        description="各任务的陈述，供报告按 section.claim_ids 显示认知类型徽标",
    )
    citation_count: int = 0


class UsageUpdatedPayload(Schema):
    usage: TokenUsage
    cost_usd: float | None = None


class PromptDigest(Schema):
    """prompt 的 hash 与长度。**不含全文**（§20.3）。"""

    hash: str
    chars: int


class AgentRunMetricsPayload(Schema):
    """一次 Agent run 的工程指标，对应 `agent_runs` 表的一行（§20.1）。

    为什么要单独一个事件类型，而不是把这些字段塞进 `AGENT_COMPLETED`：

    1. 决策 C 规定 Python 不碰业务库（§4），埋点数据只能经事件流到 Next 侧；
    2. 规划阶段的 run 没有 `task_id`，也不产生 `AGENT_COMPLETED`
       （它不是计划里的任务），塞进去就得为它伪造一个任务节点；
    3. `AGENT_COMPLETED` 是给 UI 看的，prompt hash 这类字段对界面毫无意义，
       混在一起会让前端 reducer 承载它不需要的概念。

    一个事件 = 一行，Next 侧直接 insert，不需要任何关联状态。
    """

    agent: AgentName
    task_id: str | None = None
    """规划阶段的 run 为 None。"""
    model_id: str
    status: TaskStatus
    """只会是 COMPLETED 或 FAILED。复用 TaskStatus 而非新造枚举，
    因为语义完全一致，多一个枚举就多一处要同步的 CHECK 约束。"""
    prompt: PromptDigest | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost_usd: float | None = None
    """None 表示该模型定价未知（见 `cost_usd()`），不是 0。"""
    duration_ms: int
    error: ErrorInfo | None = None


class WarningPayload(Schema):
    code: str
    message: str
    """额度不足、数据缺失等。不中断流程，但需在 UI 显示。"""


# ─────────────────────────────────────────────────────────────────────────────
# 事件信封
# ─────────────────────────────────────────────────────────────────────────────


class EventEnvelope(Schema):
    """所有事件的公共字段（§12.3）。"""

    seq: int = Field(description="会话内单调递增，用于顺序保证与断线重连")
    session_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    message: str | None = Field(
        default=None, description="面向用户的一句话，前端可直接显示（§12.3）"
    )


class SessionStartedEvent(EventEnvelope):
    type: Literal[EventType.SESSION_STARTED] = EventType.SESSION_STARTED
    payload: SessionStartedPayload


class SessionCompletedEvent(EventEnvelope):
    type: Literal[EventType.SESSION_COMPLETED] = EventType.SESSION_COMPLETED
    payload: SessionCompletedPayload


class SessionFailedEvent(EventEnvelope):
    type: Literal[EventType.SESSION_FAILED] = EventType.SESSION_FAILED
    payload: SessionFailedPayload


class SessionCancelledEvent(EventEnvelope):
    type: Literal[EventType.SESSION_CANCELLED] = EventType.SESSION_CANCELLED
    payload: None = None


class IntentClassifiedEvent(EventEnvelope):
    type: Literal[EventType.INTENT_CLASSIFIED] = EventType.INTENT_CLASSIFIED
    payload: IntentClassifiedPayload


class PlanCreatedEvent(EventEnvelope):
    type: Literal[EventType.PLAN_CREATED] = EventType.PLAN_CREATED
    payload: PlanCreatedPayload


class PlanUpdatedEvent(EventEnvelope):
    type: Literal[EventType.PLAN_UPDATED] = EventType.PLAN_UPDATED
    payload: PlanUpdatedPayload


class StageChangedEvent(EventEnvelope):
    type: Literal[EventType.STAGE_CHANGED] = EventType.STAGE_CHANGED
    payload: StageChangedPayload


class AgentStartedEvent(EventEnvelope):
    type: Literal[EventType.AGENT_STARTED] = EventType.AGENT_STARTED
    payload: AgentStartedPayload


class AgentProgressEvent(EventEnvelope):
    type: Literal[EventType.AGENT_PROGRESS] = EventType.AGENT_PROGRESS
    payload: AgentProgressPayload


class AgentReasoningEvent(EventEnvelope):
    type: Literal[EventType.AGENT_REASONING] = EventType.AGENT_REASONING
    payload: AgentReasoningPayload


class AgentCompletedEvent(EventEnvelope):
    type: Literal[EventType.AGENT_COMPLETED] = EventType.AGENT_COMPLETED
    payload: AgentCompletedPayload


class AgentFailedEvent(EventEnvelope):
    type: Literal[EventType.AGENT_FAILED] = EventType.AGENT_FAILED
    payload: AgentFailedPayload


class AgentHandoffEvent(EventEnvelope):
    type: Literal[EventType.AGENT_HANDOFF] = EventType.AGENT_HANDOFF
    payload: AgentHandoffPayload


class ToolStartedEvent(EventEnvelope):
    type: Literal[EventType.TOOL_STARTED] = EventType.TOOL_STARTED
    payload: ToolStartedPayload


class ToolCompletedEvent(EventEnvelope):
    type: Literal[EventType.TOOL_COMPLETED] = EventType.TOOL_COMPLETED
    payload: ToolCompletedPayload


class ToolFailedEvent(EventEnvelope):
    type: Literal[EventType.TOOL_FAILED] = EventType.TOOL_FAILED
    payload: ToolFailedPayload


class SourceFoundEvent(EventEnvelope):
    type: Literal[EventType.SOURCE_FOUND] = EventType.SOURCE_FOUND
    payload: SourceFoundPayload


class MetricFoundEvent(EventEnvelope):
    type: Literal[EventType.METRIC_FOUND] = EventType.METRIC_FOUND
    payload: MetricFoundPayload


class FactCheckStartedEvent(EventEnvelope):
    type: Literal[EventType.FACT_CHECK_STARTED] = EventType.FACT_CHECK_STARTED
    payload: FactCheckStartedPayload


class FactCheckProgressEvent(EventEnvelope):
    type: Literal[EventType.FACT_CHECK_PROGRESS] = EventType.FACT_CHECK_PROGRESS
    payload: FactCheckProgressPayload


class ClaimVerifiedEvent(EventEnvelope):
    type: Literal[EventType.CLAIM_VERIFIED] = EventType.CLAIM_VERIFIED
    payload: ClaimVerifiedPayload


class ConflictDetectedEvent(EventEnvelope):
    type: Literal[EventType.CONFLICT_DETECTED] = EventType.CONFLICT_DETECTED
    payload: ConflictDetectedPayload


class ReportStartedEvent(EventEnvelope):
    type: Literal[EventType.REPORT_STARTED] = EventType.REPORT_STARTED
    payload: None = None


class ReportSectionDeltaEvent(EventEnvelope):
    type: Literal[EventType.REPORT_SECTION_DELTA] = EventType.REPORT_SECTION_DELTA
    payload: ReportSectionDeltaPayload


class ReportCompletedEvent(EventEnvelope):
    type: Literal[EventType.REPORT_COMPLETED] = EventType.REPORT_COMPLETED
    payload: ReportCompletedPayload


class UsageUpdatedEvent(EventEnvelope):
    type: Literal[EventType.USAGE_UPDATED] = EventType.USAGE_UPDATED
    payload: UsageUpdatedPayload


class AgentRunMetricsEvent(EventEnvelope):
    type: Literal[EventType.AGENT_RUN_METRICS] = EventType.AGENT_RUN_METRICS
    payload: AgentRunMetricsPayload
    """埋点专用，前端 UI 应忽略（reducer 的 default 分支）。"""


class WarningEvent(EventEnvelope):
    type: Literal[EventType.WARNING] = EventType.WARNING
    payload: WarningPayload


class HeartbeatEvent(EventEnvelope):
    type: Literal[EventType.HEARTBEAT] = EventType.HEARTBEAT
    payload: None = None
    """保活，防代理超时。前端应忽略。"""


ResearchEvent = Annotated[
    SessionStartedEvent
    | SessionCompletedEvent
    | SessionFailedEvent
    | SessionCancelledEvent
    | IntentClassifiedEvent
    | PlanCreatedEvent
    | PlanUpdatedEvent
    | StageChangedEvent
    | AgentStartedEvent
    | AgentProgressEvent
    | AgentReasoningEvent
    | AgentCompletedEvent
    | AgentFailedEvent
    | AgentHandoffEvent
    | ToolStartedEvent
    | ToolCompletedEvent
    | ToolFailedEvent
    | SourceFoundEvent
    | MetricFoundEvent
    | FactCheckStartedEvent
    | FactCheckProgressEvent
    | ClaimVerifiedEvent
    | ConflictDetectedEvent
    | ReportStartedEvent
    | ReportSectionDeltaEvent
    | ReportCompletedEvent
    | UsageUpdatedEvent
    | AgentRunMetricsEvent
    | WarningEvent
    | HeartbeatEvent,
    Field(discriminator="type"),
]
"""判别联合。反序列化用 `ResearchEventAdapter.validate_python/json`。"""

ResearchEventAdapter: TypeAdapter[ResearchEvent] = TypeAdapter(ResearchEvent)

TERMINAL_EVENT_TYPES = frozenset(
    {
        EventType.SESSION_COMPLETED,
        EventType.SESSION_FAILED,
        EventType.SESSION_CANCELLED,
    }
)
"""收到这三种事件后事件流结束，前端可关闭连接。"""
