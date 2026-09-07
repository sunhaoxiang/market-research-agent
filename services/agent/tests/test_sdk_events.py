"""P1-7 SDK 事件翻译层测试。

主线测试走真实的 `Runner.run_streamed` + SDK 自带的 `ScriptedModel`——
手搓 SDK 事件对象只能验证我对 SDK 结构的**假设**，而假设正是这一层最容易错的
地方。事实上第一版就是这么发现问题的：原先自建的 FakeModel 只实现了
`get_response`，而 `run_streamed` 走 `stream_response`，13 个测试一次性报错。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from agents import (
    Agent,
    AgentUpdatedStreamEvent,
    RawResponsesStreamEvent,
    RunItemStreamEvent,
    Runner,
    Tool,
    function_tool,
)
from agents.items import TResponseOutputItem
from agents.testing import ScriptedModel, assistant_message, function_call

from agent_service.observability.event_bus import EventBus
from agent_service.observability.sdk_events import (
    MAX_SUMMARY_CHARS,
    AgentRunTranslator,
)
from agent_service.schemas.common import AgentName
from agent_service.schemas.events import (
    EventType,
    ResearchEvent,
    ToolCompletedPayload,
    ToolFailedPayload,
    ToolStartedPayload,
)
from agent_service.schemas.tools import (
    DataProvenance,
    ToolError,
    ToolErrorCode,
    ToolResult,
)


@function_tool
def get_price(symbol: str) -> str:
    """查询价格。"""
    return f"{symbol} = 42.0"


@function_tool
def get_tvl(protocol: str) -> ToolResult[dict[str, float]]:
    """查询 TVL，返回符合 §8.1 的 ToolResult。"""
    return ToolResult.success(
        data={"tvl_usd": 1_234_567.0},
        provenance=DataProvenance(
            provider="defillama",
            endpoint="/protocol/hyperliquid",
            retrieved_at=datetime(2026, 9, 7, tzinfo=UTC),
            is_cached=True,
        ),
    )


@function_tool
def failing_tool(symbol: str) -> ToolResult[None]:
    """总是失败，用于验证失败路径。"""
    return ToolResult.failure(
        ToolError(code=ToolErrorCode.QUOTA_EXHAUSTED, message="CoinGecko 免费额度已用尽")
    )


def _bus() -> EventBus:
    return EventBus("sess-1", heartbeat_interval_s=60.0)


def _translator(bus: EventBus, task_id: str = "t1") -> AgentRunTranslator:
    return AgentRunTranslator(bus, agent=AgentName.CRYPTO_RESEARCH, task_id=task_id)


def _calls_then_reply(*calls: TResponseOutputItem, reply: str = "完成") -> ScriptedModel:
    """一轮工具调用，随后一轮文本答复。"""
    return ScriptedModel([list(calls), [assistant_message(reply)]])


def _payload[T](event: ResearchEvent, payload_type: type[T]) -> T:
    """取出并收窄 payload。

    比 `event.payload.field` 加一堆 pyright 忽略注解好：既让类型检查通过，
    又把「payload 类型符合预期」也纳入断言——压制注解只会把问题藏起来。
    """
    payload = event.payload
    assert isinstance(payload, payload_type), (
        f"期望 {payload_type.__name__}，实际是 {type(payload).__name__}"
    )
    return payload


async def _run(model: ScriptedModel, tools: list[Tool], bus: EventBus) -> list[ResearchEvent]:
    """跑一次流式 run，返回翻译产出的事件。"""
    agent = Agent(
        name=AgentName.CRYPTO_RESEARCH.value,
        instructions="测试",
        tools=tools,
        model=model,
    )
    translator = _translator(bus)
    result = Runner.run_streamed(agent, "问题")

    emitted: list[ResearchEvent] = []
    async for event in result.stream_events():
        translated = translator.translate(event)
        if translated is not None:
            emitted.append(translated)
    return emitted


# ─── 端到端（ScriptedModel 驱动真实 Runner）─────────────────────────────────


async def test_tool_call_translates_to_started_and_completed() -> None:
    model = _calls_then_reply(
        function_call("get_price", {"symbol": "HYPE"}, call_id="call_a"),
        reply="查询完成。",
    )
    events = await _run(model, [get_price], _bus())

    assert [e.type for e in events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
    ]


async def test_started_and_completed_share_the_same_call_id() -> None:
    """前端要靠 call_id 把两条事件配成一个活动条目（§13.1）。"""
    model = _calls_then_reply(function_call("get_price", {"symbol": "HYPE"}, call_id="call_a"))
    started, completed = await _run(model, [get_price], _bus())

    assert (
        _payload(started, ToolStartedPayload).call_id
        == _payload(completed, ToolCompletedPayload).call_id
    )


async def test_completed_event_carries_the_real_tool_name() -> None:
    """回归：`tool_output` 的 raw_item 是 function_call_output，**不带 name**。

    早期实现在这里退回读 `type`，于是完成事件里的工具名变成
    "function_call_output"，活动面板会把所有工具显示成同一个名字。
    正确做法是在 tool_called 时记下名字、输出时按 call_id 回查。
    """
    model = _calls_then_reply(function_call("get_price", {"symbol": "HYPE"}, call_id="call_a"))
    started, completed = await _run(model, [get_price], _bus())

    assert _payload(started, ToolStartedPayload).tool == "get_price"
    assert _payload(completed, ToolCompletedPayload).tool == "get_price"


async def test_translated_events_carry_agent_and_task_context() -> None:
    """context 来自编排层而非 SDK 反解——这是本层的设计前提。"""
    model = _calls_then_reply(function_call("get_price", {"symbol": "HYPE"}, call_id="call_a"))
    started = (await _run(model, [get_price], _bus()))[0]

    payload = _payload(started, ToolStartedPayload)
    assert payload.agent is AgentName.CRYPTO_RESEARCH
    assert payload.task_id == "t1"


async def test_tool_result_provenance_reaches_the_event() -> None:
    """§8.1 的 ToolResult 带 provider 与 cache_hit，活动面板要显示它们。"""
    model = _calls_then_reply(
        function_call("get_tvl", {"protocol": "hyperliquid"}, call_id="call_a")
    )
    _, completed = await _run(model, [get_tvl], _bus())

    payload = _payload(completed, ToolCompletedPayload)
    assert payload.provider == "defillama"
    assert payload.cache_hit is True
    assert payload.ok is True


async def test_failing_tool_result_becomes_tool_failed_with_its_code() -> None:
    """QUOTA_EXHAUSTED 必须与其他错误区分——它决定要不要快速失败（§8.2）。"""
    model = _calls_then_reply(function_call("failing_tool", {"symbol": "HYPE"}, call_id="call_a"))
    events = await _run(model, [failing_tool], _bus())

    assert [e.type for e in events] == [EventType.TOOL_STARTED, EventType.TOOL_FAILED]
    failed = _payload(events[1], ToolFailedPayload)
    assert failed.error_code is ToolErrorCode.QUOTA_EXHAUSTED
    assert "额度" in failed.message


async def test_plain_string_tool_output_degrades_to_success() -> None:
    """SDK 内置工具与 Phase 2 之前的临时工具不返回 ToolResult。"""
    model = _calls_then_reply(function_call("get_price", {"symbol": "HYPE"}, call_id="call_a"))
    _, completed = await _run(model, [get_price], _bus())

    payload = _payload(completed, ToolCompletedPayload)
    assert payload.ok is True
    assert payload.provider is None
    assert "42.0" in (payload.result_summary or "")


async def test_final_message_does_not_produce_an_event() -> None:
    """刻意忽略 message_output_created：编排层会把最终答复变成
    agent_completed，这里再发一条会让活动面板出现重复条目。"""
    model = ScriptedModel([[assistant_message("直接回答，不调工具。")]])
    events = await _run(model, [get_price], _bus())

    assert events == []


async def test_parallel_tool_calls_are_paired_by_call_id() -> None:
    """并行工具调用是常态（§7.2 允许 parallel_tool_calls），
    配对必须靠 call_id 而不是顺序。"""
    model = _calls_then_reply(
        function_call("get_price", {"symbol": "HYPE"}, call_id="call_a"),
        function_call("get_price", {"symbol": "SOL"}, call_id="call_b"),
    )
    events = await _run(model, [get_price], _bus())

    started = {
        _payload(e, ToolStartedPayload).call_id for e in events if e.type is EventType.TOOL_STARTED
    }
    completed = {
        _payload(e, ToolCompletedPayload).call_id
        for e in events
        if e.type is EventType.TOOL_COMPLETED
    }

    assert started == {"call_a", "call_b"}
    assert completed == {"call_a", "call_b"}


async def test_events_land_on_the_bus_with_monotonic_seq() -> None:
    """翻译层必须经总线分配 seq，不能自己造。"""
    bus = _bus()
    model = _calls_then_reply(function_call("get_price", {"symbol": "HYPE"}, call_id="call_a"))
    events = await _run(model, [get_price], bus)

    assert [e.seq for e in events] == [1, 2]
    assert all(e.session_id == "sess-1" for e in events)


async def test_duration_is_recorded_for_completed_tools() -> None:
    model = _calls_then_reply(function_call("get_price", {"symbol": "HYPE"}, call_id="call_a"))
    _, completed = await _run(model, [get_price], _bus())

    assert _payload(completed, ToolCompletedPayload).duration_ms >= 0


# ─── 摘要与截断 ──────────────────────────────────────────────────────────────


async def test_long_tool_output_is_truncated() -> None:
    """事件会推给前端并落库，完整入参/结果可能是几十 KB（§12.2）。"""

    @function_tool
    def verbose_tool(seed: str) -> str:
        """返回超长内容。"""
        return "x" * (MAX_SUMMARY_CHARS * 10)

    model = _calls_then_reply(function_call("verbose_tool", {"seed": "s"}, call_id="call_a"))
    _, completed = await _run(model, [verbose_tool], _bus())

    summary = _payload(completed, ToolCompletedPayload).result_summary or ""
    assert len(summary) <= MAX_SUMMARY_CHARS + 1
    assert summary.endswith("…")


async def test_long_tool_input_is_truncated() -> None:
    model = _calls_then_reply(
        function_call("get_price", {"symbol": "H" * (MAX_SUMMARY_CHARS * 5)}, call_id="call_a")
    )
    started = (await _run(model, [get_price], _bus()))[0]

    input_summary = _payload(started, ToolStartedPayload).input_summary or ""
    assert len(input_summary) <= MAX_SUMMARY_CHARS + 1


# ─── 健壮性：这一层永不抛异常 ────────────────────────────────────────────────


def test_unknown_stream_event_is_ignored() -> None:
    """SDK 升级可能引入新事件类型，不能因此中断研究会话。"""

    class Unknown:
        type = "brand_new_event"

    assert _translator(_bus()).translate(Unknown()) is None  # pyright: ignore[reportArgumentType]


def test_malformed_run_item_is_ignored() -> None:
    """raw_item 形状不符时记 warning 并跳过，绝不抛异常。"""

    class NotAToolCall:
        raw_item = object()

    event = RunItemStreamEvent(name="tool_called", item=NotAToolCall())  # pyright: ignore[reportArgumentType]

    assert _translator(_bus()).translate(event) is None


def test_raw_response_events_are_ignored() -> None:
    """token 级增量：报告正文由 Report Writer 自己发 report_section_delta
    （它知道 section_id，这里不知道）。"""

    event = RawResponsesStreamEvent(data={"type": "response.output_text.delta"})  # pyright: ignore[reportArgumentType]

    assert _translator(_bus()).translate(event) is None


async def test_translation_never_raises_on_a_tool_that_errors() -> None:
    """工具抛异常时 SDK 会把错误文本作为 tool_output 送回；
    翻译层要正常产出事件，而不是把异常继续往上抛。"""

    @function_tool
    def exploding_tool(symbol: str) -> str:
        """总是抛异常。"""
        msg = "上游 503"
        raise RuntimeError(msg)

    model = _calls_then_reply(function_call("exploding_tool", {"symbol": "X"}, call_id="call_a"))
    events = await _run(model, [exploding_tool], _bus())

    assert EventType.TOOL_STARTED in [e.type for e in events]
    assert len(events) == 2


@pytest.mark.parametrize("bad_name", ["", "Crypto Research", "unknown_agent"])
def test_unknown_agent_name_does_not_break_the_stream(bad_name: str) -> None:
    """Agent 用了协议外的名字属于编程错误，但翻译层不能因此杀掉会话。"""

    translator = _translator(_bus())
    event = AgentUpdatedStreamEvent(new_agent=Agent(name=bad_name, model=ScriptedModel()))

    assert translator.translate(event) is None
    assert translator.agent is AgentName.CRYPTO_RESEARCH  # 保持原值，不被污染
