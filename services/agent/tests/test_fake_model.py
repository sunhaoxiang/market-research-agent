"""P1-8 FakeModel 测试。

FakeModel 是整个测试策略的地基，它自己出错会让上层测试给出误导性的绿灯，
所以它的行为需要独立锁住。
"""

from __future__ import annotations

import pytest
from agents import Agent, ModelSettings, Runner, function_tool

from agent_service.testing import FakeModel, FakeTurn, message, tool_call


@function_tool
def lookup(symbol: str) -> str:
    """查询标的。"""
    return f"{symbol}: ok"


async def test_drives_a_complete_run_with_tool_call() -> None:
    """P1-8 的验收标准：能驱动一次完整 run。"""
    model = FakeModel(
        [
            FakeTurn(output=[tool_call("lookup", {"symbol": "HYPE"})]),
            "HYPE 查询完成。",
        ]
    )
    agent = Agent(name="t", instructions="测试", tools=[lookup], model=model)

    result = await Runner.run(agent, "查 HYPE")

    assert result.final_output == "HYPE 查询完成。"
    assert model.call_count == 2


async def test_records_what_the_agent_actually_sent() -> None:
    """录制的调用是断言 prompt 与 settings 的唯一手段。"""
    model = FakeModel(["done"])
    agent = Agent(
        name="t",
        instructions="系统提示词",
        tools=[lookup],
        model=model,
        model_settings=ModelSettings(temperature=0.3),
    )

    await Runner.run(agent, "问题")

    call = model.last_call
    assert call.system_instructions == "系统提示词"
    assert call.tool_names == ["lookup"]
    assert call.model_settings.temperature == pytest.approx(0.3)


async def test_repeats_last_turn_when_script_runs_out() -> None:
    """多数测试只关心前几轮，收尾轮次重复比强迫精确数轮数更实用。"""
    model = FakeModel(["only turn"])
    agent = Agent(name="t", instructions="x", model=model)

    assert (await Runner.run(agent, "a")).final_output == "only turn"
    assert (await Runner.run(agent, "b")).final_output == "only turn"
    assert model.call_count == 2


async def test_raising_turn_propagates() -> None:
    """用于测试编排层的失败降级：任一子任务失败不应中断整个流程（§7.2）。"""
    model = FakeModel([FakeTurn(output=[], raises=RuntimeError("上游 503"))])
    agent = Agent(name="t", instructions="x", model=model)

    with pytest.raises(RuntimeError, match="上游 503"):
        await Runner.run(agent, "a")


async def test_queue_allows_incremental_scripting() -> None:
    model = FakeModel()
    model.queue(FakeTurn(output=[tool_call("lookup", {"symbol": "X"})])).queue("完成")
    agent = Agent(name="t", instructions="x", tools=[lookup], model=model)

    assert (await Runner.run(agent, "a")).final_output == "完成"


def test_tool_call_serializes_dict_arguments() -> None:
    call = tool_call("lookup", {"symbol": "HYPE"})
    assert call["arguments"] == '{"symbol": "HYPE"}'
    assert tool_call("lookup", '{"raw": 1}')["arguments"] == '{"raw": 1}'


def test_message_builds_valid_output_item() -> None:
    item = message("你好")
    assert item["role"] == "assistant"
    assert item["content"][0]["text"] == "你好"


def test_last_call_before_any_call_fails_loudly() -> None:
    with pytest.raises(AssertionError, match="尚未被调用"):
        _ = FakeModel().last_call


def test_stream_response_explains_why_it_is_unimplemented() -> None:
    model = FakeModel(["x"])
    with pytest.raises(NotImplementedError, match="RunItemStreamEvent"):
        model.stream_response(None, "x", ModelSettings(), [], None, [], None)  # pyright: ignore[reportArgumentType]
