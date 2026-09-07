"""`FakeModel` —— 测试策略的核心（§18.2）。

实现 SDK 的 `Model` 协议，按脚本逐轮返回预设输出。它让下面这些**没有 LLM 也能测**：

  - 编排层的分层 fan-out、超时、失败降级
  - 事件翻译层（`RunItemStreamEvent` → 本协议事件）
  - 结构化输出的重试路径：脚本第一轮返回坏 JSON、第二轮返回好的，
    断言重试机制真的把它救回来了——这种场景用真实模型根本没法稳定复现

同时它也是**成本控制手段**：调 UI、改事件协议、调报告排版时全程零 token。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agents import Model, ModelResponse, ModelSettings, Usage
from agents.items import TResponseInputItem, TResponseOutputItem, TResponseStreamEvent

if TYPE_CHECKING:
    from agents import AgentOutputSchemaBase, Handoff, ModelTracing, Tool
    from openai.types.responses import ResponsePromptParam


def message(text: str) -> dict[str, Any]:
    """一条助手文本消息。"""
    return {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def tool_call(
    name: str, arguments: dict[str, Any] | str, call_id: str = "call_fake"
) -> dict[str, Any]:
    """一次函数调用。`arguments` 传 dict 会自动序列化。"""
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return {
        "id": "fc_fake",
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": raw,
    }


@dataclass
class FakeTurn:
    """一轮模型响应。"""

    output: list[dict[str, Any]]
    usage: Usage = field(default_factory=lambda: Usage(requests=1, input_tokens=0, output_tokens=0))
    raises: Exception | None = None
    """设置后本轮直接抛出，用于测试失败降级路径。"""


@dataclass
class RecordedCall:
    """FakeModel 收到的一次调用，供断言 prompt 与 settings 是否符合预期。"""

    system_instructions: str | None
    input: str | list[TResponseInputItem]
    model_settings: ModelSettings
    tool_names: list[str]
    output_schema_name: str | None


class FakeModel(Model):
    """按脚本逐轮返回预设输出。

    轮次用尽后会重复最后一轮而不是报错——多数测试只关心前几轮，
    让它在收尾轮次上无限重复比强迫每个测试精确数轮数更实用。
    传空脚本则返回空消息。
    """

    def __init__(self, turns: Sequence[FakeTurn | str] | None = None) -> None:
        self._turns: list[FakeTurn] = [
            FakeTurn(output=[message(turn)]) if isinstance(turn, str) else turn
            for turn in (turns or [])
        ]
        self.calls: list[RecordedCall] = []

    def queue(self, turn: FakeTurn | str) -> FakeModel:
        """追加一轮。返回 self 以便链式调用。"""
        self._turns.append(FakeTurn(output=[message(turn)]) if isinstance(turn, str) else turn)
        return self

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_call(self) -> RecordedCall:
        if not self.calls:
            msg = "FakeModel 尚未被调用过"
            raise AssertionError(msg)
        return self.calls[-1]

    def _next_turn(self) -> FakeTurn:
        if not self._turns:
            return FakeTurn(output=[message("")])
        index = min(len(self.calls) - 1, len(self._turns) - 1)
        return self._turns[index]

    def _record(
        self,
        system_instructions: str | None,
        input_items: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
    ) -> None:
        self.calls.append(
            RecordedCall(
                system_instructions=system_instructions,
                input=input_items,
                model_settings=model_settings,
                tool_names=[tool.name for tool in tools],
                output_schema_name=output_schema.name() if output_schema else None,
            )
        )

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: ResponsePromptParam | None = None,
    ) -> ModelResponse:
        self._record(system_instructions, input, model_settings, tools, output_schema)
        turn = self._next_turn()
        if turn.raises is not None:
            raise turn.raises

        return ModelResponse(
            output=[_as_output_item(item) for item in turn.output],
            usage=turn.usage,
            response_id="resp_fake",
        )

    def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: ResponsePromptParam | None = None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        # 刻意用普通 def 而非 async def：SDK 接口签名返回 AsyncIterator，
        # 写成 async def 会让调用方拿到 coroutine 而不是可迭代对象，
        # 报错信息变成难懂的 "'coroutine' object is not an async iterator"
        raise NotImplementedError(_STREAM_HINT)


_STREAM_HINT = (
    "FakeModel 不实现 stream_response。编排层用 Runner.run_streamed 时，"
    "事件来自 RunItemStreamEvent（由 SDK 从 get_response 的结果合成），"
    "不需要模型侧的 token 流——需要测 token 级流式再补。"
)


def _as_output_item(item: dict[str, Any]) -> TResponseOutputItem:
    """dict → SDK 的输出项。

    SDK 的 `ModelResponse` 是 pydantic dataclass，会自行校验并转换这些 dict，
    所以这里直接透传即可；写成函数是为了让类型忽略集中在一处。
    """
    return item  # pyright: ignore[reportReturnType]
