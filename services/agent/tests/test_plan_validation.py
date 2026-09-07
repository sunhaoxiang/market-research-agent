"""计划语义校验（P1-9）。

分成三组：**必须拒绝**、**应当修复**、**分层结果**。
"""

from __future__ import annotations

import pytest

from agent_service.orchestrator.plan_validation import (
    PlanRejectedError,
    validate_plan,
)
from agent_service.schemas.common import AgentName, QuestionType
from agent_service.schemas.plan import ResearchPlan, ResearchTask
from agent_service.testing import IsolatedExecutionLimits


def _task(task_id: str, *, depends_on: list[str] | None = None, priority: int = 0) -> ResearchTask:
    return ResearchTask(
        id=task_id,
        agent=AgentName.CRYPTO_RESEARCH,
        objective=f"任务 {task_id}",
        depends_on=depends_on or [],
        priority=priority,
    )


def _plan(*tasks: ResearchTask) -> ResearchPlan:
    return ResearchPlan(
        question_type=QuestionType.CRYPTO,
        interpretation="测试用计划",
        tasks=list(tasks),
    )


def _limits(max_tasks: int = 6) -> IsolatedExecutionLimits:
    return IsolatedExecutionLimits(max_tasks_per_plan=max_tasks)


# ── 必须拒绝 ─────────────────────────────────────────────────────────────────


def test_empty_plan_is_rejected() -> None:
    with pytest.raises(PlanRejectedError, match="没有任何研究任务"):
        validate_plan(_plan(), _limits())


def test_duplicate_ids_are_rejected_rather_than_renamed() -> None:
    """重命名会让 depends_on 指向哪一个变成猜测，因此宁可拒绝。"""
    with pytest.raises(PlanRejectedError, match=r"\['t1'\]"):
        validate_plan(_plan(_task("t1"), _task("t1")), _limits())


# ── 应当修复 ─────────────────────────────────────────────────────────────────


def test_dangling_dependency_is_dropped_not_fatal() -> None:
    validated = validate_plan(_plan(_task("t1", depends_on=["t9"])), _limits())

    assert validated.plan.tasks[0].depends_on == []
    assert [issue.code for issue in validated.issues] == ["invalid_dependency"]


def test_self_dependency_is_dropped() -> None:
    validated = validate_plan(_plan(_task("t1", depends_on=["t1"])), _limits())

    assert validated.plan.tasks[0].depends_on == []
    assert validated.issues[0].code == "invalid_dependency"


def test_cycle_is_broken_by_dropping_backward_edges() -> None:
    """t1 → t2 → t1 的环里，只有指向后方的那条边被删。"""
    validated = validate_plan(
        _plan(_task("t1", depends_on=["t2"]), _task("t2", depends_on=["t1"])),
        _limits(),
    )

    by_id = {task.id: task for task in validated.plan.tasks}
    assert by_id["t1"].depends_on == []  # 指向排在后面的 t2，被删
    assert by_id["t2"].depends_on == ["t1"]  # 指向前方，保留
    assert validated.issues[0].code == "dependency_cycle"


def test_three_node_cycle_is_broken() -> None:
    validated = validate_plan(
        _plan(
            _task("t1", depends_on=["t3"]),
            _task("t2", depends_on=["t1"]),
            _task("t3", depends_on=["t2"]),
        ),
        _limits(),
    )

    # 环被打断后一定能分层——分层本身就是无环的证明
    assert [[task.id for task in layer] for layer in validated.layers] == [["t1"], ["t2"], ["t3"]]


def test_excess_tasks_are_dropped_by_priority() -> None:
    plan = _plan(
        _task("t1", priority=0),
        _task("t2", priority=5),
        _task("t3", priority=1),
    )
    validated = validate_plan(plan, _limits(max_tasks=2))

    # 保留 priority 最高的两个，但按原顺序输出
    assert [task.id for task in validated.plan.tasks] == ["t2", "t3"]
    assert validated.issues[0].code == "too_many_tasks"
    assert "t1" in validated.issues[0].detail


def test_truncation_cleans_up_dependencies_it_orphaned() -> None:
    """截断可能删掉别人的依赖，因此依赖清理必须在截断之后跑。

    这是回归测试：先清理再截断的话，t2 会留下一条指向已被删除的 t3 的依赖，
    执行器分层时会永远等不到它。
    """
    plan = _plan(
        _task("t1", priority=9),
        _task("t2", priority=9, depends_on=["t3"]),
        _task("t3", priority=0),
    )
    validated = validate_plan(plan, _limits(max_tasks=2))

    assert [task.id for task in validated.plan.tasks] == ["t1", "t2"]
    assert validated.plan.tasks[1].depends_on == []
    assert {issue.code for issue in validated.issues} == {"too_many_tasks", "invalid_dependency"}


def test_valid_plan_passes_through_untouched() -> None:
    plan = _plan(_task("t1"), _task("t2", depends_on=["t1"]))
    validated = validate_plan(plan, _limits())

    assert validated.issues == ()
    assert validated.plan == plan


def test_repair_is_deterministic() -> None:
    """同一份坏计划两次修复结果必须一致，否则线上问题无法复现。"""
    plan = _plan(
        _task("t1", depends_on=["t2", "t9"]),
        _task("t2", depends_on=["t1"]),
        _task("t3", depends_on=["t3"]),
    )

    first = validate_plan(plan, _limits())
    second = validate_plan(plan, _limits())

    assert first.plan == second.plan
    assert first.issues == second.issues


# ── 分层 ─────────────────────────────────────────────────────────────────────


def test_independent_tasks_land_in_one_layer() -> None:
    """默认并行：没有依赖就该在同一层，否则执行器会白白串行。"""
    validated = validate_plan(_plan(_task("t1"), _task("t2"), _task("t3")), _limits())

    assert [[task.id for task in layer] for layer in validated.layers] == [["t1", "t2", "t3"]]


def test_diamond_dependency_collapses_to_three_layers() -> None:
    validated = validate_plan(
        _plan(
            _task("t1"),
            _task("t2", depends_on=["t1"]),
            _task("t3", depends_on=["t1"]),
            _task("t4", depends_on=["t2", "t3"]),
        ),
        _limits(),
    )

    assert [[task.id for task in layer] for layer in validated.layers] == [
        ["t1"],
        ["t2", "t3"],
        ["t4"],
    ]


def test_layers_cover_every_task_exactly_once() -> None:
    validated = validate_plan(
        _plan(_task("t1"), _task("t2", depends_on=["t1"]), _task("t3", depends_on=["t1"])),
        _limits(),
    )

    flattened = [task.id for layer in validated.layers for task in layer]
    assert sorted(flattened) == ["t1", "t2", "t3"]
