"""Gap Check（§7.1 步骤 7，P5-6 / D8）。

纯代码规则，不打模型：看 findings 里声明的可补缺口、以及失败且没有 salvage
的任务。能补的才生成补充任务；「尚未实现 / 没有免费 API / 超时」这类补了
也不会变的缺口直接留给报告的数据限制章节。

一轮最多加几个任务，由 `max_tasks_per_plan` 剩余名额卡住。调用方负责
`max_supplement_rounds` 硬上限，避免无限研究循环。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_service.schemas.common import AgentName, AssetType, QuestionType
from agent_service.schemas.entities import Entity
from agent_service.schemas.findings import ResearchFinding
from agent_service.schemas.plan import ResearchTask

if TYPE_CHECKING:
    from agent_service.config import ExecutionLimits
    from agent_service.orchestrator.state import ResearchState

_FILLABLE_MARKERS = (
    "未找到",
    "未能获取",
    "搜索失败",
    "正文抽不出",
)
"""可补缺口的白名单。

「未获得任何来源」是 assemble_finding 在没打到工具时的默认缺口，几乎每次
空跑都会出现；拿它当补充条件会把第一轮成功的 crypto+web 再打一遍。
超时 / 尚未实现 / unsupported 补了也不会变，同样排除。
"""

_MAX_GAPS_IN_OBJECTIVE = 4
_MAX_TASKS_PER_ROUND = 2
_MIN_REMAINING_S = 20.0


@dataclass(frozen=True)
class Gap:
    """一处值得补一轮的缺口。"""

    code: str
    detail: str
    source_task_id: str | None
    source_agent: AgentName | None
    entities: tuple[Entity, ...] = ()


def fillable_gaps(findings: Sequence[ResearchFinding]) -> list[str]:
    """供测试与报告侧复用：只返回声明里能再搜一次的缺口。"""
    return [gap for finding in findings for gap in finding.data_gaps if _is_fillable(gap)]


def detect_gaps(state: ResearchState) -> list[Gap]:
    """扫描 findings 里声明的可补缺口。

    失败且没有 salvage 的任务不在这里补：那是执行器已经降级过的路径，
    再加一轮很容易把 429/超时再打一遍。补充只针对 Agent 明确写出的可检索缺口。
    """
    found: list[Gap] = []
    plan_tasks = state.plan.plan.tasks if state.plan is not None else ()
    by_id = {task.id: task for task in plan_tasks}

    for finding in state.findings:
        origin = by_id.get(finding.task_id)
        entities = tuple(origin.entities) if origin is not None else ()
        for gap in finding.data_gaps:
            if not _is_fillable(gap):
                continue
            found.append(
                Gap(
                    code="declared",
                    detail=gap,
                    source_task_id=finding.task_id,
                    source_agent=finding.agent,
                    entities=entities,
                )
            )
    return found


def propose_supplement_tasks(
    state: ResearchState,
    limits: ExecutionLimits,
    *,
    gaps: Sequence[Gap] | None = None,
) -> list[ResearchTask]:
    """把缺口收成补充任务。预算或名额不够时返回空列表。"""
    detected = list(gaps) if gaps is not None else detect_gaps(state)
    if not detected:
        return []
    if state.remaining_s(limits.total_timeout_s) < _MIN_REMAINING_S:
        return []
    if state.over_budget(limits.max_session_cost_usd):
        state.warn_budget(
            limits.max_session_cost_usd,
            message=f"会话成本已达上限 ${limits.max_session_cost_usd:.2f}，不再补充研究",
        )
        return []

    existing = list(state.plan.plan.tasks) if state.plan is not None else []
    remaining_slots = limits.max_tasks_per_plan - len(existing)
    if remaining_slots <= 0:
        return []

    question_type = (
        state.plan.plan.question_type if state.plan is not None else QuestionType.GENERIC
    )
    plan_entities = tuple(state.plan.plan.entities) if state.plan is not None else ()
    used_ids = {task.id for task in existing}
    next_index = _next_task_index(used_ids)
    budget = min(_MAX_TASKS_PER_ROUND, remaining_slots)

    grouped: dict[AgentName, list[Gap]] = {}
    for gap in detected:
        agent = _pick_agent(gap.source_agent, question_type, plan_entities or gap.entities)
        if agent is None:
            continue
        grouped.setdefault(agent, []).append(gap)

    tasks: list[ResearchTask] = []
    for agent, group in grouped.items():
        if len(tasks) >= budget:
            break
        task_id = f"t{next_index}"
        next_index += 1
        entities = _entities_for(group, plan_entities)
        depends_on = [
            gap.source_task_id
            for gap in group
            if gap.source_task_id is not None and gap.source_task_id in used_ids
        ]
        used_ids.add(task_id)
        tasks.append(
            ResearchTask(
                id=task_id,
                agent=agent,
                objective=_objective_for(group),
                entities=list(entities),
                suggested_tools=_suggested_tools(agent),
                depends_on=list(dict.fromkeys(depends_on)),
                priority=2,
            )
        )
    return tasks


def _is_fillable(gap: str) -> bool:
    return any(marker in gap for marker in _FILLABLE_MARKERS)


def _pick_agent(
    origin: AgentName | None,
    question_type: QuestionType,
    entities: Sequence[Entity],
) -> AgentName | None:
    """缺口来自哪类 Agent，就换另一类去补，避免把同一套失败的工具再打一遍。"""
    if origin is AgentName.CRYPTO_RESEARCH or origin is AgentName.STOCK_RESEARCH:
        return AgentName.WEB_RESEARCH
    if origin is AgentName.WEB_RESEARCH:
        return _specialist_for(question_type, entities)
    if origin is AgentName.FACT_CHECKER or origin is AgentName.REPORT_WRITER:
        return None
    return _specialist_for(question_type, entities) or AgentName.WEB_RESEARCH


def _specialist_for(question_type: QuestionType, entities: Sequence[Entity]) -> AgentName | None:
    if question_type is QuestionType.CRYPTO:
        return AgentName.CRYPTO_RESEARCH
    if question_type is QuestionType.STOCK:
        return AgentName.STOCK_RESEARCH
    kinds = {entity.type for entity in entities}
    if AssetType.CRYPTO in kinds and AssetType.STOCK not in kinds:
        return AgentName.CRYPTO_RESEARCH
    if AssetType.STOCK in kinds:
        return AgentName.STOCK_RESEARCH
    return None


def _entities_for(group: Sequence[Gap], plan_entities: Sequence[Entity]) -> tuple[Entity, ...]:
    collected: list[Entity] = []
    seen: set[tuple[str, str]] = set()
    for gap in group:
        for entity in gap.entities:
            key = (entity.type.value, entity.symbol)
            if key in seen:
                continue
            seen.add(key)
            collected.append(entity)
    if collected:
        return tuple(collected)
    return tuple(plan_entities)


def _objective_for(group: Sequence[Gap]) -> str:
    details = list(dict.fromkeys(gap.detail for gap in group))[:_MAX_GAPS_IN_OBJECTIVE]
    joined = "；".join(details)
    return f"补充检索关键缺口：{joined}"


def _suggested_tools(agent: AgentName) -> list[str]:
    if agent is AgentName.WEB_RESEARCH:
        return ["web_search", "web_fetch"]
    if agent is AgentName.CRYPTO_RESEARCH:
        return ["get_tvl", "get_market_data"]
    if agent is AgentName.STOCK_RESEARCH:
        return ["get_earnings_summary", "get_valuation_metrics"]
    return []


def _next_task_index(used_ids: set[str]) -> int:
    highest = 0
    for task_id in used_ids:
        if task_id.startswith("t") and task_id[1:].isdigit():
            highest = max(highest, int(task_id[1:]))
    return highest + 1
