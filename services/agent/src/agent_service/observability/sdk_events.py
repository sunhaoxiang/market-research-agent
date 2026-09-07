"""SDK 流事件 → 本协议事件的翻译层（§12.1-5，P1-7）。

**为什么要翻译而不透传**：`RunItemStreamEvent` 的形状由 SDK 决定，
`item.raw_item` 直接就是 OpenAI 的 wire format。透传等于让前端 reducer
耦合 SDK 内部结构——SDK 升级、或换 provider 导致 raw_item 形状变化时，
坏的是 UI 而不是一处适配代码。

**这一层永不抛异常。** 它处在事件流的关键路径上，一次未捕获的异常等于
杀掉整个研究会话。所以所有字段提取都是防御式的：认不出的事件降级为
debug 日志，认不出的字段降级为 None，绝不让格式意外中断研究。

**翻译器的作用域是「一次 agent run」。** agent 名与 task_id 由编排层
在构造时给出，而不是从 SDK 事件里反解——编排层本来就知道自己在跑哪个任务，
从 SDK 内部结构里再猜一遍既脆弱又多余。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import structlog
from agents import (
    AgentUpdatedStreamEvent,
    RawResponsesStreamEvent,
    RunItemStreamEvent,
)
from agents.items import (
    HandoffOutputItem,
    ReasoningItem,
    ToolCallItem,
    ToolCallOutputItem,
)

from agent_service.schemas.common import AgentName
from agent_service.schemas.events import (
    AgentHandoffEvent,
    AgentHandoffPayload,
    AgentReasoningEvent,
    AgentReasoningPayload,
    ToolCompletedEvent,
    ToolCompletedPayload,
    ToolFailedEvent,
    ToolFailedPayload,
    ToolStartedEvent,
    ToolStartedPayload,
)
from agent_service.schemas.tools import ToolErrorCode

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from agents.stream_events import StreamEvent

    from agent_service.observability.event_bus import EventBus
    from agent_service.schemas.events import ResearchEvent

log = structlog.get_logger(__name__)

MAX_SUMMARY_CHARS = 200
"""入参/结果摘要的截断长度。

事件流会整条推给前端并落库，完整入参可能是几十 KB 的 JSON。
§12.2 明确要求这里放摘要而非完整内容。
"""

_UNTRACKED_DURATION_MS = 0
"""收到 tool_output 但没有对应的 tool_called 记录时的兜底时长。"""


class AgentRunTranslator:
    """把一次 agent run 的 SDK 事件流翻译成本协议事件。

    用法（编排层）::

        translator = AgentRunTranslator(bus, agent=AgentName.CRYPTO_RESEARCH, task_id="t1")
        result = Runner.run_streamed(agent, question)
        await translator.pump(result.stream_events())
    """

    def __init__(
        self,
        bus: EventBus,
        *,
        agent: AgentName,
        task_id: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bus = bus
        self._agent = agent
        self._task_id = task_id
        self._clock = clock
        # call_id → (工具名, 开始时刻)。工具名必须在 tool_called 时记下：
        # tool_output 的 raw_item 是 function_call_output，**不带 name 字段**，
        # 只能从这里回查，否则事件里的 tool 会退化成 "function_call_output"。
        self._in_flight: dict[str, tuple[str, float]] = {}

    @property
    def agent(self) -> AgentName:
        """当前 agent。发生 handoff 后会更新。"""
        return self._agent

    async def pump(self, stream: AsyncIterator[StreamEvent]) -> None:
        """消费整个 SDK 事件流并翻译。"""
        async for event in stream:
            self.translate(event)

    def translate(self, event: StreamEvent) -> ResearchEvent | None:
        """翻译单个事件。返回 None 表示该事件在本协议里无对应物。

        返回值主要给测试用；编排层通常只关心副作用（事件已进总线）。
        """
        if isinstance(event, RunItemStreamEvent):
            return self._translate_run_item(event)

        if isinstance(event, AgentUpdatedStreamEvent):
            # 只更新内部状态，不发事件：用户可见的 agent 切换由
            # handoff_occured 表达，两处都发会让前端收到重复的切换信号
            self._track_agent_change(event.new_agent.name)
            return None

        if isinstance(event, RawResponsesStreamEvent):
            # token 级增量。报告正文的流式输出由 Report Writer 自己发
            # report_section_delta（它知道 section_id，这里不知道），
            # 其余 agent 的 token 流对 UI 没有价值。
            return None

        log.debug("sdk_events.unknown_stream_event", event_type=type(event).__name__)
        return None

    # ── RunItem 分派 ────────────────────────────────────────────────────────

    def _translate_run_item(self, event: RunItemStreamEvent) -> ResearchEvent | None:
        match event.name:
            case "tool_called":
                return self._on_tool_called(event.item)
            case "tool_output":
                return self._on_tool_output(event.item)
            case "reasoning_item_created":
                return self._on_reasoning(event.item)
            case "handoff_occured":
                return self._on_handoff(event.item)
            case "message_output_created":
                # 刻意忽略：在工具循环里这几乎总是 agent 的最终答复，
                # 编排层会把它变成 agent_completed（带 claim/source 计数）。
                # 这里再发一条 agent_progress 只会让活动面板出现重复条目。
                return None
            case _:
                # handoff_requested（handoff_occured 已含双方信息）、
                # mcp_* 与 tool_search_*（本项目未使用）
                log.debug("sdk_events.ignored_run_item", name=event.name)
                return None

    def _on_tool_called(self, item: Any) -> ResearchEvent | None:
        if not isinstance(item, ToolCallItem):
            return self._unexpected_item("tool_called", item)

        call_id = _call_id(item.raw_item)
        if call_id is None:
            return self._unexpected_item("tool_called", item, reason="缺少 call_id")

        tool = _tool_name(item.raw_item)
        self._in_flight[call_id] = (tool, self._clock())

        return self._bus.emit(
            ToolStartedEvent,
            message=f"调用 {tool}",
            payload=ToolStartedPayload(
                call_id=call_id,
                tool=tool,
                agent=self._agent,
                task_id=self._task_id,
                input_summary=_summarize(_tool_arguments(item.raw_item)),
            ),
        )

    def _on_tool_output(self, item: Any) -> ResearchEvent | None:
        if not isinstance(item, ToolCallOutputItem):
            return self._unexpected_item("tool_output", item)

        call_id = _call_id(item.raw_item)
        if call_id is None:
            return self._unexpected_item("tool_output", item, reason="缺少 call_id")

        tool, duration_ms = self._finish(call_id)
        outcome = _interpret_output(item.output)

        if not outcome.ok:
            return self._bus.emit(
                ToolFailedEvent,
                message=f"{tool} 失败：{outcome.summary}",
                payload=ToolFailedPayload(
                    call_id=call_id,
                    tool=tool,
                    error_code=outcome.error_code or ToolErrorCode.UPSTREAM_ERROR,
                    message=outcome.summary or "工具返回失败但未提供原因",
                ),
            )

        return self._bus.emit(
            ToolCompletedEvent,
            message=f"{tool} 完成",
            payload=ToolCompletedPayload(
                call_id=call_id,
                tool=tool,
                ok=True,
                provider=outcome.provider,
                cache_hit=outcome.cache_hit,
                duration_ms=duration_ms,
                result_summary=outcome.summary,
            ),
        )

    def _on_reasoning(self, item: Any) -> ResearchEvent | None:
        if not isinstance(item, ReasoningItem):
            return self._unexpected_item("reasoning_item_created", item)

        summary = _reasoning_summary(item.raw_item)
        if not summary:
            return None

        return self._bus.emit(
            AgentReasoningEvent,
            payload=AgentReasoningPayload(
                agent=self._agent,
                task_id=self._task_id,
                summary=summary,
            ),
        )

    def _on_handoff(self, item: Any) -> ResearchEvent | None:
        if not isinstance(item, HandoffOutputItem):
            return self._unexpected_item("handoff_occured", item)

        source = _as_agent_name(item.source_agent.name)
        target = _as_agent_name(item.target_agent.name)
        if source is None or target is None:
            return None  # _as_agent_name 已记日志

        self._agent = target
        return self._bus.emit(
            AgentHandoffEvent,
            message=f"{source.value} → {target.value}",
            payload=AgentHandoffPayload(from_agent=source, to_agent=target),
        )

    # ── 辅助 ────────────────────────────────────────────────────────────────

    def _finish(self, call_id: str) -> tuple[str, int]:
        """取出该调用的工具名与耗时。"""
        tracked = self._in_flight.pop(call_id, None)
        if tracked is None:
            # 例如断线重连后半程接管，或 SDK 只发了 output。工具名与时长都
            # 不可信，但没有理由因此丢掉整个事件。
            log.debug("sdk_events.tool_output_without_start", call_id=call_id)
            return "unknown", _UNTRACKED_DURATION_MS
        tool, started = tracked
        return tool, int((self._clock() - started) * 1000)

    def _unexpected_item(self, name: str, item: object, reason: str = "类型不符") -> None:
        """SDK 事件与预期结构不符。记日志并跳过，绝不中断事件流。"""
        log.warning(
            "sdk_events.unexpected_item",
            name=name,
            item_type=type(item).__name__,
            reason=reason,
            agent=self._agent.value,
            task_id=self._task_id,
        )

    def _track_agent_change(self, raw_name: str) -> None:
        agent = _as_agent_name(raw_name)
        if agent is not None:
            self._agent = agent


# ─────────────────────────────────────────────────────────────────────────────
# 字段提取：全部防御式，raw_item 可能是 dict 也可能是 pydantic 模型
# ─────────────────────────────────────────────────────────────────────────────


def _field(raw: object, name: str) -> Any:
    """从 dict 或对象里取字段。

    `raw_item` 的具体类型随 provider 与工具种类变化（function_call、
    computer_call、hosted tool 各不相同），逐一 isinstance 既冗长又会在
    SDK 加新类型时漏掉。这里只取需要的字段，取不到就返回 None。
    """
    if isinstance(raw, dict):
        return raw.get(name)
    return getattr(raw, name, None)


def _call_id(raw: object) -> str | None:
    value = _field(raw, "call_id") or _field(raw, "id")
    return value if isinstance(value, str) and value else None


def _tool_name(raw: object) -> str:
    value = _field(raw, "name")
    if isinstance(value, str) and value:
        return value
    # hosted tool（如 web_search_call）不带 name，只有 type
    type_value = _field(raw, "type")
    return type_value if isinstance(type_value, str) and type_value else "unknown"


def _tool_arguments(raw: object) -> str | None:
    value = _field(raw, "arguments")
    if value is None or isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _summarize(text: str | None) -> str | None:
    """截断到 MAX_SUMMARY_CHARS。事件会推给前端并落库，不能放完整内容。"""
    if not text:
        return None
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    return text[:MAX_SUMMARY_CHARS] + "…"


def _reasoning_summary(raw: object) -> str | None:
    """提取推理摘要。

    ⚠️ DeepSeek 的 `reasoning_content` 是**完整思维链**而非摘要，可达数千
    token（实测 planner 单次 169-3000+ reasoning token）。整条推给前端既
    浪费带宽也没有阅读价值，因此这里统一截断。
    """
    summary = _field(raw, "summary")
    if isinstance(summary, list):
        parts = [text for part in summary if (text := _field(part, "text"))]
        return _summarize("\n".join(str(part) for part in parts))

    content = _field(raw, "content") or _field(raw, "text")
    return _summarize(content if isinstance(content, str) else None)


def _as_agent_name(raw_name: str) -> AgentName | None:
    """SDK 的 agent 名（自由字符串）→ 本协议的枚举。

    对不上说明 Agent 构造时用了协议外的名字，属于编程错误；但翻译层
    不能因此抛异常中断会话，记 warning 并跳过该事件。
    """
    try:
        return AgentName(raw_name)
    except ValueError:
        log.warning(
            "sdk_events.unknown_agent_name",
            raw_name=raw_name,
            known=[member.value for member in AgentName],
        )
        return None


class _ToolOutcome:
    """从工具返回值里读出的结果信息。"""

    __slots__ = ("cache_hit", "error_code", "ok", "provider", "summary")

    def __init__(
        self,
        *,
        ok: bool,
        summary: str | None = None,
        provider: str | None = None,
        cache_hit: bool = False,
        error_code: ToolErrorCode | None = None,
    ) -> None:
        self.ok = ok
        self.summary = summary
        self.provider = provider
        self.cache_hit = cache_hit
        self.error_code = error_code


def _interpret_output(output: object) -> _ToolOutcome:
    """解读工具返回值。

    §8.1 规定所有 tool 返回 `ToolResult`，其中带 ok / error / provenance，
    能直接填出 provider 与 cache_hit。但这里用鸭子类型而非 isinstance：
    `ToolResult` 是泛型模型，`isinstance(x, ToolResult)` 对参数化版本不可靠。
    非 ToolResult 的返回值（Phase 2 之前的临时工具、SDK 内置工具）降级为
    「成功 + 字符串摘要」。
    """
    ok = _field(output, "ok")
    if not isinstance(ok, bool):
        return _ToolOutcome(ok=True, summary=_summarize(_stringify(output)))

    if not ok:
        error = _field(output, "error")
        code = _field(error, "code")
        return _ToolOutcome(
            ok=False,
            summary=_summarize(_stringify(_field(error, "message"))),
            error_code=code if isinstance(code, ToolErrorCode) else _parse_error_code(code),
        )

    provenance = _field(output, "provenance")
    provider = _field(provenance, "provider")
    # 字段名是 is_cached（见 schemas/tools.py DataProvenance），不是 cache_hit——
    # 事件 payload 那侧才叫 cache_hit，两者刻意在此处对齐
    is_cached = _field(provenance, "is_cached")

    return _ToolOutcome(
        ok=True,
        summary=_summarize(_stringify(_field(output, "data"))),
        provider=provider if isinstance(provider, str) else None,
        cache_hit=bool(is_cached),
    )


def _parse_error_code(code: object) -> ToolErrorCode | None:
    if not isinstance(code, str):
        return None
    try:
        return ToolErrorCode(code)
    except ValueError:
        log.debug("sdk_events.unknown_tool_error_code", code=code)
        return None


def _stringify(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    dump = getattr(value, "model_dump_json", None)
    if callable(dump):
        try:
            return str(dump())
        except (TypeError, ValueError):
            pass
    return str(value)
