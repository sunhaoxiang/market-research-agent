"""研究计划的语义校验（§7.2 / §7.3，P1-9）。

Pydantic 只能保证**结构**合法（字段齐全、agent 是合法枚举值）。它管不了：
任务数是否超限、`depends_on` 引用的任务是否存在、依赖图是否成环。这些错误
会一路传到执行器才炸，那时已经花了 planner 的 token。

**为什么以修复为主而非直接拒绝**：§7.2 规定 planner 失败即整体失败。而
"引用了一个不存在的任务 id" 这类 LLM 笔误，对研究结果的影响其实微乎其微——
丢掉这条依赖，任务照样能跑，只是排序不再最优。为此杀掉整个会话（用户还得
重新等一次 planner）明显不划算。

所以只有真正**无法安全修复**的才拒绝：

- 任务列表为空：没有任何东西可研究
- 任务 id 重复：`depends_on` 指向哪一个无从判断，任何修复都是猜

其余全部修复并记录到 `issues`，由编排层发 warning 事件让用户可见。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from agent_service.schemas.plan import ResearchPlan, ResearchTask

if TYPE_CHECKING:
    from agent_service.config import ExecutionLimits

log = structlog.get_logger(__name__)


class PlanRejectedError(ValueError):
    """计划存在无法安全修复的问题。"""

    def __init__(self, reason: str) -> None:
        super().__init__(f"研究计划不可用：{reason}")
        self.reason = reason


@dataclass(frozen=True)
class PlanIssue:
    """一处被修复的问题。"""

    code: str
    detail: str


@dataclass(frozen=True)
class ValidatedPlan:
    plan: ResearchPlan
    issues: tuple[PlanIssue, ...] = ()
    """修复记录。非空说明模型输出有瑕疵，值得记 warning 并在 eval 里统计。"""

    @property
    def layers(self) -> tuple[tuple[ResearchTask, ...], ...]:
        """按依赖分层，供执行器分层 fan-out（§7.2）。

        同一层内的任务彼此无依赖，可以并行；层与层之间必须串行。
        计划已保证无环，因此这里一定能分完。
        """
        return _layer(self.plan.tasks)


def validate_plan(plan: ResearchPlan, limits: ExecutionLimits) -> ValidatedPlan:
    """校验并尽可能修复计划。无法修复时抛 `PlanRejectedError`。"""
    if not plan.tasks:
        raise PlanRejectedError("没有任何研究任务")

    ids = [task.id for task in plan.tasks]
    if len(ids) != len(set(ids)):
        duplicates = sorted({task_id for task_id in ids if ids.count(task_id) > 1})
        raise PlanRejectedError(
            f"任务 id 重复：{duplicates}。depends_on 指向哪一个无从判断，无法安全修复"
        )

    issues: list[PlanIssue] = []
    tasks = _truncate(plan.tasks, limits.max_tasks_per_plan, issues)
    tasks = _clean_dependencies(tasks, issues)
    tasks = _break_cycles(tasks, issues)

    if issues:
        log.warning(
            "plan.repaired",
            issue_codes=[issue.code for issue in issues],
            task_count=len(tasks),
        )

    return ValidatedPlan(plan=plan.model_copy(update={"tasks": tasks}), issues=tuple(issues))


# ─────────────────────────────────────────────────────────────────────────────
# 各项修复
# ─────────────────────────────────────────────────────────────────────────────


def _truncate(
    tasks: list[ResearchTask], max_tasks: int, issues: list[PlanIssue]
) -> list[ResearchTask]:
    """超限时按 priority 降序保留。

    刻意不是简单截掉尾部：priority 是模型对重要性的判断，应当尊重。
    priority 相同时保持原顺序（`sorted` 稳定），因为模型通常先写重要的。
    """
    if len(tasks) <= max_tasks:
        return tasks

    ranked = sorted(tasks, key=lambda task: -task.priority)
    kept_ids = {task.id for task in ranked[:max_tasks]}
    # 用原顺序输出，保持 t1/t2/… 的可读性
    kept = [task for task in tasks if task.id in kept_ids]

    dropped = [task.id for task in tasks if task.id not in kept_ids]
    issues.append(
        PlanIssue(
            code="too_many_tasks",
            detail=f"任务数 {len(tasks)} 超过上限 {max_tasks}，已丢弃 {dropped}",
        )
    )
    return kept


def _clean_dependencies(tasks: list[ResearchTask], issues: list[PlanIssue]) -> list[ResearchTask]:
    """丢掉自引用与指向不存在任务的依赖。

    截断之后必须再跑一次：被丢掉的任务可能正是别人的依赖。
    """
    known = {task.id for task in tasks}
    cleaned: list[ResearchTask] = []

    for task in tasks:
        kept = [dep for dep in task.depends_on if dep in known and dep != task.id]
        if len(kept) != len(task.depends_on):
            removed = [dep for dep in task.depends_on if dep not in kept]
            issues.append(
                PlanIssue(
                    code="invalid_dependency",
                    detail=f"{task.id} 的依赖 {removed} 不存在或指向自己，已移除",
                )
            )
            task = _with_depends_on(task, kept)  # noqa: PLW2901
        cleaned.append(task)

    return cleaned


def _break_cycles(tasks: list[ResearchTask], issues: list[PlanIssue]) -> list[ResearchTask]:
    """打断依赖环。

    做法是按任务在计划里出现的顺序定一个基准序，凡是"依赖了排在自己后面的
    任务"的边就删掉。这必然消除所有环（剩下的边都指向前方，构成偏序），
    而且删除是确定性的——同一份计划每次修复结果一致，便于复现与测试。

    代价是可能删掉某些本来合法的"后向依赖"，但那种写法本身就违反了
    prompt 里"id 顺序编号"的约定，且任务丢掉依赖仍能执行。
    """
    order = {task.id: index for index, task in enumerate(tasks)}
    if not _has_cycle(tasks):
        return tasks

    rebuilt: list[ResearchTask] = []
    for task in tasks:
        kept = [dep for dep in task.depends_on if order[dep] < order[task.id]]
        if len(kept) != len(task.depends_on):
            removed = [dep for dep in task.depends_on if dep not in kept]
            issues.append(
                PlanIssue(
                    code="dependency_cycle",
                    detail=f"{task.id} 的依赖 {removed} 会形成环，已移除",
                )
            )
            task = _with_depends_on(task, kept)  # noqa: PLW2901
        rebuilt.append(task)

    return rebuilt


def _with_depends_on(task: ResearchTask, depends_on: list[str]) -> ResearchTask:
    """返回替换了 depends_on 的副本。"""
    return task.model_copy(update={"depends_on": depends_on})


# ─────────────────────────────────────────────────────────────────────────────
# 图算法
# ─────────────────────────────────────────────────────────────────────────────


def _has_cycle(tasks: list[ResearchTask]) -> bool:
    """Kahn 算法：能拓扑排序完即无环。"""
    return sum(len(layer) for layer in _layer_or_partial(tasks)) != len(tasks)


def _layer(tasks: list[ResearchTask]) -> tuple[tuple[ResearchTask, ...], ...]:
    layers = _layer_or_partial(tasks)
    placed = sum(len(layer) for layer in layers)
    if placed != len(tasks):
        # validate_plan 之后不应发生；真发生了说明有人绕过校验直接分层
        msg = f"依赖图有环，{len(tasks) - placed} 个任务无法分层。请先经 validate_plan 修复"
        raise PlanRejectedError(msg)
    return layers


def _layer_or_partial(
    tasks: list[ResearchTask],
) -> tuple[tuple[ResearchTask, ...], ...]:
    """分层。有环时只返回能排出的部分，供 `_has_cycle` 判断。

    `pending` 用 dict 而非 set 是为了保留插入顺序：同层任务按计划里的原顺序
    输出，执行顺序与日志都可预测。
    """
    by_id = {task.id: task for task in tasks}
    pending = {task.id: {dep for dep in task.depends_on if dep in by_id} for task in tasks}
    layers: list[tuple[ResearchTask, ...]] = []
    done: set[str] = set()

    while pending:
        ready = [task_id for task_id, deps in pending.items() if deps <= done]
        if not ready:
            break  # 剩下的都在环里
        layers.append(tuple(by_id[task_id] for task_id in ready))
        done.update(ready)
        for task_id in ready:
            del pending[task_id]

    return tuple(layers)
