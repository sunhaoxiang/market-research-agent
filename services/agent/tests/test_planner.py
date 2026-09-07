"""Research Manager 与规划阶段（P1-9）。

用 `ScriptedModel` 跑真实的 `Runner`，因此覆盖的是完整链路：
prompt 渲染 → json_mode 的 schema 追加 → 文本解析 → 语义校验 → 事件发布。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from agents.testing import ScriptedModel, assistant_message
from pydantic import SecretStr

from agent_service.agents.research_manager import (
    PROMPT_NAME,
    PlannerAgent,
    build_research_manager,
)
from agent_service.models.capabilities import StructuredOutputMode
from agent_service.models.registry import ModelRegistry
from agent_service.observability.event_bus import EventBus
from agent_service.orchestrator.plan_validation import PlanRejectedError
from agent_service.orchestrator.planner import create_plan
from agent_service.prompts import render_prompt
from agent_service.schemas.common import AgentName, QuestionType
from agent_service.schemas.events import (
    EventType,
    IntentClassifiedPayload,
    PlanCreatedPayload,
    ResearchEvent,
    WarningPayload,
)
from agent_service.testing import (
    IsolatedExecutionLimits,
    IsolatedProviderCredentials,
    IsolatedSettings,
)

_NOW = datetime(2026, 9, 7, tzinfo=UTC)


def _plan_json(**overrides: Any) -> str:
    """一份合法计划的 JSON 文本，模拟模型输出。"""
    body: dict[str, Any] = {
        "question_type": "crypto",
        "interpretation": "用户想了解 Hyperliquid 的协议收入与代币解锁情况",
        "entities": [{"type": "crypto", "symbol": "HYPE", "name": "Hyperliquid"}],
        "tasks": [
            {
                "id": "t1",
                "agent": "crypto_research",
                "objective": "获取 Hyperliquid 过去 90 天的手续费收入",
                "entities": [],
                "suggested_tools": ["get_protocol_fees"],
                "depends_on": [],
                "priority": 1,
            },
            {
                "id": "t2",
                "agent": "web_research",
                "objective": "查找 HYPE 的代币解锁时间表",
                "entities": [],
                "suggested_tools": [],
                "depends_on": [],
                "priority": 0,
            },
        ],
        "report_sections": ["Overview", "Fundamentals", "Risks"],
        "assumptions": ["「最近」按过去 90 天处理"],
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


def _limits(max_tasks: int = 6) -> IsolatedExecutionLimits:
    return IsolatedExecutionLimits(max_tasks_per_plan=max_tasks)


def _registry(planner_model: str | None = None) -> ModelRegistry:
    """带假 DeepSeek key 的隔离 registry。

    key 是假的但必须存在：`for_role` 会在缺 key 时抛 `ProviderUnavailableError`，
    而模型实例本身不会被调用——真实请求由 `ScriptedModel` 顶掉。
    """
    return ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test")),
            model_role_planner=planner_model,
        )
    )


def _scripted_planner(*replies: str, limits: IsolatedExecutionLimits | None = None) -> PlannerAgent:
    """把真实的 Research Manager 接到脚本模型上。

    刻意复用 `build_research_manager` 而不是自己拼一个 Agent：prompt 渲染与
    策略选择本身就是待测行为，绕过它们等于什么都没测。
    """
    built = build_research_manager(_registry(), limits or _limits())
    scripted = ScriptedModel([[assistant_message(reply)] for reply in replies])
    return PlannerAgent(
        agent=built.agent.clone(model=scripted),
        strategy=built.strategy,
        model_id=built.model_id,
    )


def _scripted(planner: PlannerAgent) -> ScriptedModel:
    model = planner.agent.model
    assert isinstance(model, ScriptedModel)
    return model


def _payload[T](event: ResearchEvent, payload_type: type[T]) -> T:
    payload = event.payload
    assert isinstance(payload, payload_type)
    return payload


# ── Agent 构造 ───────────────────────────────────────────────────────────────


def test_planner_uses_the_planner_role_model() -> None:
    """换模型只改环境变量——role 覆盖必须生效（§9.5）。"""
    registry = _registry("deepseek:deepseek-v4-flash")
    built = build_research_manager(registry, _limits())

    assert built.model_id == "deepseek:deepseek-v4-flash"


def test_planner_has_no_tools() -> None:
    """规划者配了工具就会「先查一下再规划」，把成本推到规划阶段。"""
    built = build_research_manager(_registry(), _limits())

    assert built.agent.tools == []
    assert built.agent.name == AgentName.RESEARCH_MANAGER.value


def test_planner_runs_json_mode_against_deepseek() -> None:
    """DeepSeek 拒绝 json_schema（实测 HTTP 400），必须落到 json_mode。"""
    built = build_research_manager(_registry(), _limits())

    assert built.strategy.mode is StructuredOutputMode.JSON_MODE
    assert built.strategy.needs_manual_parsing


def test_prompt_states_the_configured_task_limit() -> None:
    """上限只在 prompt 里写死会与配置漂移，模型规划 6 个而代码只留 3 个。"""
    built = build_research_manager(_registry(), _limits(max_tasks=3))

    assert isinstance(built.agent.instructions, str)
    assert "最多 3 个任务" in built.agent.instructions
    assert "{{" not in built.agent.instructions


def test_prompt_is_byte_stable_across_builds() -> None:
    """§9.8：prompt 前缀一变，整个会话的缓存全部失效（命中价仅未命中的 3%）。"""
    first = render_prompt(PROMPT_NAME, max_tasks=6)
    second = render_prompt(PROMPT_NAME, max_tasks=6)

    assert first == second
    # 不能含时间戳之类每次都变的内容
    assert str(datetime.now(UTC).year) not in first


def test_prompt_lists_exactly_the_executable_agents() -> None:
    """prompt 里出现 fact_checker / report_writer 会让模型把它们规划成任务，
    而那两个阶段由流程本身负责，规划出来就是重复执行。"""
    prompt = render_prompt(PROMPT_NAME, max_tasks=6)
    executable = {
        AgentName.CRYPTO_RESEARCH,
        AgentName.STOCK_RESEARCH,
        AgentName.WEB_RESEARCH,
    }

    for agent in AgentName:
        if agent in executable:
            assert agent.value in prompt, f"{agent.value} 应出现在可用 agent 表里"
        else:
            assert agent.value not in prompt, f"{agent.value} 不该出现在 prompt 中"


def test_planner_temperature_is_low() -> None:
    """同一个问题两次得到完全不同的任务树会让人怀疑系统可靠性。"""
    built = build_research_manager(_registry(), _limits())

    assert built.agent.model_settings.temperature == 0.2


# ── 规划流程 ─────────────────────────────────────────────────────────────────


async def test_create_plan_parses_and_validates() -> None:
    result = await create_plan(
        "Hyperliquid 最近怎么样？",
        planner=_scripted_planner(_plan_json()),
        limits=_limits(),
        now=_NOW,
    )

    assert result.plan.question_type is QuestionType.CRYPTO
    assert [task.id for task in result.plan.tasks] == ["t1", "t2"]
    assert result.attempts == 1
    assert result.validated.issues == ()


async def test_current_date_goes_into_the_user_message() -> None:
    """模型知识截止日期早于当下，没有日期锚点它会把「最新财报」解析到训练数据的
    时间——而这种偏差在计划里看不出来，要等执行完拿到过期数据才发现。

    同时验证日期**不在** instructions 里：那会让 prompt 缓存每天失效一次。
    """
    planner = _scripted_planner(_plan_json())
    await create_plan("NVDA 最新财报", planner=planner, limits=_limits(), now=_NOW)

    call = _scripted(planner).calls[0]
    assert "2026-09-07" in str(call.input)
    assert "2026-09-07" not in (call.system_instructions or "")


async def test_json_mode_wiring_reaches_the_model_call() -> None:
    """端到端确认 DeepSeek 路径：发 `json_object`，且**不发** json_schema。

    后半句才是关键——SDK 只要看到 `output_type` 就会强行发 json_schema，
    而 DeepSeek 对此直接返回 HTTP 400（实测）。
    """
    planner = _scripted_planner(_plan_json())
    await create_plan("Hyperliquid", planner=planner, limits=_limits(), now=_NOW)

    call = _scripted(planner).calls[0]
    assert call.output_schema is None
    assert call.model_settings.extra_body == {"response_format": {"type": "json_object"}}
    # schema 说明追加在 instructions 末尾，保持前缀稳定（§9.8）
    instructions = call.system_instructions or ""
    assert instructions.startswith("你是一个金融研究平台的研究规划者")
    assert "JSON Schema" in instructions


async def test_malformed_output_is_retried_with_feedback() -> None:
    """json_mode 路径下模型会偶尔漏字段；把具体错误回喂比重跑一次靠谱。"""
    broken = json.dumps({"question_type": "crypto"}, ensure_ascii=False)
    result = await create_plan(
        "Hyperliquid",
        planner=_scripted_planner(broken, _plan_json()),
        limits=_limits(),
        now=_NOW,
    )

    assert result.attempts == 2
    assert len(result.plan.tasks) == 2


async def test_plan_wrapped_in_prose_is_still_parsed() -> None:
    """明确要求不要包代码块，模型仍然经常包一层。"""
    noisy = f"好的，这是研究计划：\n```json\n{_plan_json()}\n```\n希望有帮助。"
    result = await create_plan(
        "Hyperliquid",
        planner=_scripted_planner(noisy),
        limits=_limits(),
        now=_NOW,
    )

    assert len(result.plan.tasks) == 2


async def test_taskless_plan_fails_the_session() -> None:
    """§7.2：planner 失败是唯一会导致整体失败的情形之一。"""
    with pytest.raises(PlanRejectedError):
        await create_plan(
            "你好",
            planner=_scripted_planner(_plan_json(tasks=[])),
            limits=_limits(),
            now=_NOW,
        )


async def test_oversized_plan_is_truncated_not_rejected() -> None:
    tasks = [
        {
            "id": f"t{index}",
            "agent": "crypto_research",
            "objective": f"任务 {index}",
            "entities": [],
            "suggested_tools": [],
            "depends_on": [],
            "priority": 0,
        }
        for index in range(1, 9)
    ]
    result = await create_plan(
        "把所有能查的都查一遍",
        planner=_scripted_planner(_plan_json(tasks=tasks)),
        limits=_limits(max_tasks=4),
        now=_NOW,
    )

    assert len(result.plan.tasks) == 4
    assert [issue.code for issue in result.validated.issues] == ["too_many_tasks"]


# ── 事件 ─────────────────────────────────────────────────────────────────────


async def _drain(bus: EventBus) -> list[ResearchEvent]:
    """关闭后排空。

    `close()` 投递哨兵，`stream()` 会先吐完缓冲的事件再结束——
    比等 15 秒心跳超时或去碰私有队列都干净。
    """
    bus.close()
    return [event async for event in bus.stream()]


async def test_intent_event_precedes_the_plan_event() -> None:
    """前端要在完整任务树到达之前就能显示「理解成了什么」（§12.2）。"""
    bus = EventBus("sess-1", heartbeat_interval_s=60.0)
    await create_plan(
        "Hyperliquid",
        planner=_scripted_planner(_plan_json()),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )
    events = await _drain(bus)

    assert [event.type for event in events] == [
        EventType.INTENT_CLASSIFIED,
        EventType.PLAN_CREATED,
    ]
    intent = _payload(events[0], IntentClassifiedPayload)
    assert intent.question_type is QuestionType.CRYPTO
    assert [entity.symbol for entity in intent.entities] == ["HYPE"]

    # plan_created 必须带完整任务树：前端靠它一次性画出来（§12.2）
    plan = _payload(events[1], PlanCreatedPayload).plan
    assert [task.id for task in plan.tasks] == ["t1", "t2"]


async def test_repairs_surface_as_warnings() -> None:
    """静默修复等于让用户看到一份悄悄缩水的报告。"""
    tasks = [
        {
            "id": "t1",
            "agent": "crypto_research",
            "objective": "任务 1",
            "entities": [],
            "suggested_tools": [],
            "depends_on": ["t7"],
            "priority": 0,
        },
    ]
    bus = EventBus("sess-1", heartbeat_interval_s=60.0)
    await create_plan(
        "Hyperliquid",
        planner=_scripted_planner(_plan_json(tasks=tasks)),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )
    events = await _drain(bus)

    warnings = [event for event in events if event.type is EventType.WARNING]
    assert len(warnings) == 1
    assert _payload(warnings[0], WarningPayload).code == "plan.invalid_dependency"
