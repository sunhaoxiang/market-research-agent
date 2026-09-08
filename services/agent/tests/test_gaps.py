"""Gap Check（P5-6）：可补缺口才加一轮任务，不可补的留给数据限制。"""

from __future__ import annotations

from agent_service.agents.placeholder import NOT_IMPLEMENTED_GAP
from agent_service.observability.event_bus import EventBus
from agent_service.orchestrator.gaps import detect_gaps, fillable_gaps, propose_supplement_tasks
from agent_service.orchestrator.plan_validation import validate_plan
from agent_service.orchestrator.state import ResearchState
from agent_service.schemas.common import AgentName, QuestionType, TaskStatus
from agent_service.schemas.findings import ResearchFinding
from agent_service.schemas.plan import ResearchPlan, ResearchTask
from agent_service.testing import IsolatedExecutionLimits


def _limits(**overrides: float | int) -> IsolatedExecutionLimits:
    return IsolatedExecutionLimits(**overrides)  # pyright: ignore[reportArgumentType]


def _task(task_id: str, agent: AgentName = AgentName.CRYPTO_RESEARCH) -> ResearchTask:
    return ResearchTask(id=task_id, agent=agent, objective=f"任务 {task_id}")


def _state(
    *tasks: ResearchTask, question_type: QuestionType = QuestionType.CRYPTO
) -> ResearchState:
    bus = EventBus("sess-gap", heartbeat_interval_s=60.0)
    state = ResearchState("sess-gap", "查询 HYPE 的 TVL", bus=bus)
    plan = ResearchPlan(
        question_type=question_type,
        interpretation="测试",
        tasks=list(tasks),
    )
    state.attach_plan(validate_plan(plan, _limits()))
    return state


def test_fillable_gaps_allowlist() -> None:
    finding = ResearchFinding(
        task_id="t1",
        agent=AgentName.CRYPTO_RESEARCH,
        summary="缺数据",
        data_gaps=[
            "未能获取 HYPE 的 TVL",
            NOT_IMPLEMENTED_GAP,
            "巨鲸转账没有免费 API",
            "任务超过 180s 未完成，未能输出结构化发现。",
            "本次任务未获得任何来源",
        ],
    )
    assert fillable_gaps([finding]) == ["未能获取 HYPE 的 TVL"]


def test_crypto_gap_proposes_web_research() -> None:
    state = _state(_task("t1"))
    state.add_finding(
        ResearchFinding(
            task_id="t1",
            agent=AgentName.CRYPTO_RESEARCH,
            summary="没拿到 TVL",
            data_gaps=["未能获取 HYPE 的 TVL"],
        )
    )
    extra = propose_supplement_tasks(state, _limits())
    assert len(extra) == 1
    assert extra[0].id == "t2"
    assert extra[0].agent is AgentName.WEB_RESEARCH
    assert extra[0].depends_on == ["t1"]
    assert "未能获取 HYPE 的 TVL" in extra[0].objective


def test_placeholder_gap_does_not_propose() -> None:
    state = _state(_task("t1", AgentName.FACT_CHECKER))
    state.add_finding(
        ResearchFinding(
            task_id="t1",
            agent=AgentName.FACT_CHECKER,
            summary="占位",
            data_gaps=[NOT_IMPLEMENTED_GAP],
        )
    )
    assert detect_gaps(state) == []
    assert propose_supplement_tasks(state, _limits()) == []


def test_failed_empty_task_does_not_propose() -> None:
    """执行失败已由执行器降级；没有声明可补缺口就不再加一轮。"""
    state = _state(_task("t1"), _task("t2", AgentName.WEB_RESEARCH))
    state.mark("t1", TaskStatus.FAILED)
    state.add_finding(
        ResearchFinding(
            task_id="t2",
            agent=AgentName.WEB_RESEARCH,
            summary="网页结论",
        )
    )
    assert propose_supplement_tasks(state, _limits()) == []


def test_no_slots_left_skips_supplement() -> None:
    state = _state(_task("t1"))
    state.add_finding(
        ResearchFinding(
            task_id="t1",
            agent=AgentName.CRYPTO_RESEARCH,
            summary="缺",
            data_gaps=["未能获取 HYPE 的 TVL"],
        )
    )
    extra = propose_supplement_tasks(state, _limits(max_tasks_per_plan=1))
    assert extra == []
