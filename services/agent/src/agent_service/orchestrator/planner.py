"""规划阶段：用户问题 → 合法的 `ResearchPlan`（P1-9）。

这一步是 §7.2 里唯一「失败即整体失败」的环节：没有计划就没有可执行的任务。
因此这里的容错做得比别处厚：结构化输出带修正重试（`run_structured`），
语义问题尽量修复而非拒绝（`validate_plan`）。

时间信息放在 user 消息而不是 system prompt 里。这不是风格问题——
system prompt 里出现日期会让 prompt 缓存每天失效一次，而缓存命中价只有
未命中的 3%（§9.8）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from agent_service.models.structured_output import run_structured
from agent_service.orchestrator.plan_validation import ValidatedPlan, validate_plan
from agent_service.schemas.events import (
    IntentClassifiedEvent,
    IntentClassifiedPayload,
    PlanCreatedEvent,
    PlanCreatedPayload,
    WarningEvent,
    WarningPayload,
)

if TYPE_CHECKING:
    from agents.result import RunResult

    from agent_service.agents.research_manager import PlannerAgent
    from agent_service.config import ExecutionLimits
    from agent_service.observability.event_bus import EventBus
    from agent_service.schemas.plan import ResearchPlan

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PlanningResult:
    validated: ValidatedPlan
    model_id: str
    attempts: int
    """模型被调用了几次。>1 说明首次输出不合 schema。"""
    run_result: RunResult
    """原始 RunResult，供上层取 usage 做成本核算（§20.1）。"""

    @property
    def plan(self) -> ResearchPlan:
        return self.validated.plan


async def create_plan(
    question: str,
    *,
    planner: PlannerAgent,
    limits: ExecutionLimits,
    bus: EventBus | None = None,
    now: datetime | None = None,
) -> PlanningResult:
    """生成并校验研究计划。

    接收已构造好的 `PlannerAgent` 而不是 `ModelRegistry`：Agent 的构造
    （解析模型、渲染 prompt）每个会话只需做一次，而且这样一来本函数不再依赖
    凭证，离线测试可以直接塞一个 `ScriptedModel` 进去。

    `bus` 可选：单测里不关心事件，真实流程里 SSE 需要它。
    `now` 可注入，让「当前日期」在测试中可复现。
    """
    structured = await run_structured(
        planner.agent,
        _user_message(question, now or datetime.now(UTC)),
        strategy=planner.strategy,
    )
    validated = validate_plan(structured.output, limits)

    log.info(
        "plan.created",
        model_id=planner.model_id,
        attempts=structured.attempts,
        task_count=len(validated.plan.tasks),
        layer_count=len(validated.layers),
        repaired=[issue.code for issue in validated.issues],
    )

    if bus is not None:
        _emit(bus, validated)

    return PlanningResult(
        validated=validated,
        model_id=planner.model_id,
        attempts=structured.attempts,
        run_result=structured.result,
    )


def _user_message(question: str, now: datetime) -> str:
    """拼装 user 消息。

    给出当前日期是必要的：模型的知识截止日期早于当下，没有这个锚点它会把
    「最新财报」「过去 30 天」解析到训练数据的时间，而这类偏差在计划里看不
    出来，要等执行完拿到过期数据才发现。
    """
    return (
        f"当前日期：{now.strftime('%Y-%m-%d')}（UTC）\n\n"
        f"用户问题：\n{question.strip()}\n\n"
        "请给出研究计划。"
    )


def _emit(bus: EventBus, validated: ValidatedPlan) -> None:
    """发出规划阶段的事件。

    `intent_classified` 先于 `plan_created`：前端可以在完整任务树到达之前
    就把「理解成了什么问题、涉及哪些标的」显示出来，让用户尽早发现理解偏差
    （§12.2）。
    """
    plan = validated.plan
    bus.emit(
        IntentClassifiedEvent,
        payload=IntentClassifiedPayload(question_type=plan.question_type, entities=plan.entities),
        message=plan.interpretation,
    )
    bus.emit(
        PlanCreatedEvent,
        payload=PlanCreatedPayload(plan=plan),
        message=f"已生成 {len(plan.tasks)} 个研究任务",
    )
    for issue in validated.issues:
        # 修复过的地方要让用户看见：任务被丢弃或依赖被移除会影响研究覆盖面，
        # 静默修复等于让用户看到一份「悄悄缩水」的报告
        bus.emit(
            WarningEvent,
            payload=WarningPayload(code=f"plan.{issue.code}", message=issue.detail),
        )
