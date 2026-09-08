"""单次研究会话的运行时状态（§7 / §14.1，P1-10）。

**这份状态是进程内的、易失的。** §14.1 明确规定会话状态由 Next.js 侧依据
`STAGE_CHANGED` 与终态事件落库，Python 不持有权威状态。这里存的只是编排过程
自己要用的账：跑到哪一层了、哪些任务失败了、花了多少钱。

**阶段迁移与事件发布绑在一起**（`advance_to`），不给调用方分开做的机会。
分开就一定会漂移——某处改了阶段没发事件，前端的进度条就停在上一阶段，
而这种 bug 在后端日志里完全看不出来。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from agent_service.observability.cost import cost_usd, to_token_usage
from agent_service.schemas.common import Stage, TaskStatus
from agent_service.schemas.events import (
    AgentRunMetricsEvent,
    AgentRunMetricsPayload,
    ErrorInfo,
    PromptDigest,
    StageChangedEvent,
    StageChangedPayload,
    TokenUsage,
)
from agent_service.sources.registry import SourceRegistry

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_service.models.catalog import ModelEntry
    from agent_service.observability.event_bus import EventBus
    from agent_service.observability.prompts import PromptFingerprint
    from agent_service.orchestrator.plan_validation import ValidatedPlan
    from agent_service.schemas.common import AgentName
    from agent_service.schemas.findings import Conflict, ResearchFinding
    from agent_service.schemas.report import ResearchReport

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AgentRun:
    """一次 Agent run 的埋点输入（§20.1）。

    做成值对象而不是给 `record_run` 排八个关键字参数：Phase 2 起每个子 Agent
    都要构造它，参数列表越长越容易把 `duration_ms` 和 `task_id` 传错位置，
    而这种错误不会报任何异常，只会让 `/debug` 里的数字悄悄失真。
    """

    agent: AgentName
    model: ModelEntry
    """带整个目录条目而非 id：成本核算需要 `capabilities.pricing`。"""
    duration_ms: int
    usage: object = None
    """SDK 的 `Usage`。与 `token_usage` 二选一。"""
    token_usage_override: TokenUsage | None = None
    """已经算好的用量。用于一次 run 含多次模型调用的场景——
    `run_structured` 的修正重试会累加各次用量，SDK 那边拿不到总数。"""
    task_id: str | None = None
    prompt: PromptFingerprint | None = None
    error: ErrorInfo | None = None

    @property
    def token_usage(self) -> TokenUsage:
        return (
            self.token_usage_override
            if self.token_usage_override is not None
            else to_token_usage(self.usage)
        )


_STAGE_MESSAGES = {
    Stage.PLANNING: "正在理解问题并制定研究计划",
    Stage.RESEARCHING: "正在执行研究任务",
    Stage.CHECKING: "正在核查关键结论",
    Stage.WRITING: "正在撰写报告",
}
"""面向用户的一句话（§12.3 要求事件自带可直接显示的文案）。"""


class ResearchState:
    """一次研究会话的运行时账本。"""

    def __init__(
        self,
        session_id: str,
        question: str,
        *,
        bus: EventBus,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session_id = session_id
        self.question = question
        self.bus = bus
        self.source_registry = SourceRegistry(bus=bus)
        self._clock = clock
        self._started_at = clock()
        self.stage: Stage | None = None
        self.plan: ValidatedPlan | None = None
        self.task_status: dict[str, TaskStatus] = {}
        self.findings: list[ResearchFinding] = []
        self.conflicts: list[Conflict] = []
        self.report: ResearchReport | None = None
        self.usage = TokenUsage()
        self.cost_usd: float | None = None
        """None 表示尚未产生任何**已知定价**的调用。见 `record_run`。"""

    # ── 时间 ────────────────────────────────────────────────────────────────

    @property
    def elapsed_ms(self) -> int:
        return int((self._clock() - self._started_at) * 1000)

    def remaining_s(self, total_timeout_s: float) -> float:
        """整体预算还剩多少秒。可能为负。

        执行器用它把单任务超时压到 `min(task_timeout_s, 剩余)`，
        以免一个慢任务把报告撰写的时间也吃掉。
        """
        return total_timeout_s - (self._clock() - self._started_at)

    # ── 阶段 ────────────────────────────────────────────────────────────────

    def advance_to(self, stage: Stage) -> None:
        """迁移到新阶段并发出 `STAGE_CHANGED`。"""
        previous = self.stage
        self.stage = stage
        self.bus.emit(
            StageChangedEvent,
            payload=StageChangedPayload(stage=stage, previous=previous),
            message=_STAGE_MESSAGES.get(stage),
        )

    # ── 计划与任务 ──────────────────────────────────────────────────────────

    def attach_plan(self, plan: ValidatedPlan) -> None:
        self.plan = plan
        self.task_status = {task.id: TaskStatus.PENDING for task in plan.plan.tasks}

    def mark(self, task_id: str, status: TaskStatus) -> None:
        self.task_status[task_id] = status

    def failed_task_ids(self) -> set[str]:
        """失败或被跳过的任务。依赖它们的任务需要在结果里披露输入缺失。"""
        return {
            task_id
            for task_id, status in self.task_status.items()
            if status in {TaskStatus.FAILED, TaskStatus.SKIPPED}
        }

    def add_finding(self, finding: ResearchFinding) -> None:
        self.findings.append(finding)
        self.mark(finding.task_id, TaskStatus.COMPLETED)

    # ── 用量与成本 ──────────────────────────────────────────────────────────

    def record_run(self, run: AgentRun, *, at: datetime | None = None) -> None:
        """登记一次 Agent run：累加会话账本，并发出 `AGENT_RUN_METRICS`。

        逐次累加而非最后汇总，是因为成本护栏要在**下一层任务启动前**生效——
        等会话结束再算就只能事后报告超支。

        记账与发事件绑在一起（同 `advance_to` 的理由）：`agent_runs` 表是
        `/debug` 与跨模型 eval 的唯一数据源（§20.1），而 Python 不碰业务库，
        这行数据只能靠事件流过去。留一个"只记账不发事件"的口子，就一定会有
        某条路径忘记发——那条 run 的成本从此在库里查不到，且没有任何报错。
        """
        moment = at or datetime.now(UTC)
        usage = run.token_usage
        self.usage = self.usage + usage

        cost = cost_usd(run.model, usage, moment)
        if cost is not None:
            self.cost_usd = (self.cost_usd or 0.0) + cost

        self.bus.emit(
            AgentRunMetricsEvent,
            payload=AgentRunMetricsPayload(
                agent=run.agent,
                task_id=run.task_id,
                model_id=run.model.id,
                status=TaskStatus.FAILED if run.error else TaskStatus.COMPLETED,
                prompt=PromptDigest(hash=run.prompt.hash, chars=run.prompt.chars)
                if run.prompt
                else None,
                usage=usage,
                cost_usd=cost,
                duration_ms=run.duration_ms,
                error=run.error,
            ),
        )

    def over_budget(self, limit_usd: float) -> bool:
        """是否已超出会话成本上限。

        定价未知（`cost_usd is None`）时返回 False：宁可放行也不要因为目录
        缺一个价格就把会话拦死。缺失定价已在 `cost_usd()` 里记了 warning。
        """
        return self.cost_usd is not None and self.cost_usd >= limit_usd
