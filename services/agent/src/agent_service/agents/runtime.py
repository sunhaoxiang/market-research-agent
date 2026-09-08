"""带工具的子 Agent 运行：流式翻译事件 + 结构化输出（含一次 JSON 修正）。

规划 Agent 没有工具，走 `run_structured`（`Runner.run`）即可。带工具的子 Agent
（Web / Crypto Research）必须 `run_streamed`，否则 Activity Panel 看不到 tool 事件。

JSON 解析失败时不再跑工具：把对话历史加上字段错误回喂，让模型只改输出形状。
再搜一遍既贵又可能改来源编号。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agents import Agent, Runner
from pydantic import BaseModel, ValidationError

from agent_service.models.structured_output import (
    EmptyOutputError,
    StructuredOutputError,
    apply_strategy,
    format_validation_feedback,
)
from agent_service.observability.cost import to_token_usage
from agent_service.schemas.events import TokenUsage

if TYPE_CHECKING:
    from agents.items import TResponseInputItem

    from agent_service.models.structured_output import StructuredOutputStrategy
    from agent_service.observability.sdk_events import AgentRunTranslator
    from agent_service.tools.deps import ToolDeps


@dataclass(frozen=True)
class ToolAgentRun[T: BaseModel]:
    output: T
    attempts: int
    usage: TokenUsage


async def run_tool_agent[T: BaseModel](
    agent: Agent[Any],
    user_input: str | list[TResponseInputItem],
    *,
    strategy: StructuredOutputStrategy[T],
    deps: ToolDeps,
    translator: AgentRunTranslator,
    max_turns: int,
) -> ToolAgentRun[T]:
    prepared = apply_strategy(agent, strategy)
    streamed = Runner.run_streamed(prepared, user_input, context=deps, max_turns=max_turns)
    await translator.pump(streamed.stream_events())
    usage = to_token_usage(streamed.context_wrapper.usage)

    parsed, raw, error = _try_parse(streamed.final_output, strategy)
    if parsed is not None:
        return ToolAgentRun(output=parsed, attempts=1, usage=usage)

    if error is not None:
        feedback = format_validation_feedback(error)
    else:
        feedback = "输出为空，请只返回 JSON。"
    fixer = prepared.clone(tools=[])
    follow_up = [*streamed.to_input_list(), {"role": "user", "content": feedback}]
    second = await Runner.run(fixer, follow_up, context=deps)
    usage = usage + to_token_usage(second.context_wrapper.usage)

    parsed, raw, error = _try_parse(second.final_output, strategy)
    if parsed is not None:
        return ToolAgentRun(output=parsed, attempts=2, usage=usage)

    raise StructuredOutputError(
        model_name=strategy.output_model.__name__,
        attempts=2,
        last_error=str(error) if error is not None else "无法解析",
        raw=raw,
        usage=usage,
    )


def _try_parse[T: BaseModel](
    final_output: object, strategy: StructuredOutputStrategy[T]
) -> tuple[T | None, str, Exception | None]:
    if isinstance(final_output, strategy.output_model):
        return final_output, "", None
    if not strategy.needs_manual_parsing:
        try:
            return strategy.output_model.model_validate(final_output), "", None
        except (ValidationError, TypeError, ValueError) as error:
            return None, str(final_output or ""), error
    raw = str(final_output or "")
    try:
        return strategy.parse(raw), raw, None
    except (EmptyOutputError, ValidationError, ValueError) as error:
        return None, raw, error
