"""会话状态与成本核算（P1-10）。

成本这部分的测试重点是**缓存命中的分开计价**：SDK 的 `input_tokens` 包含
缓存部分，混算会让 `MAX_SESSION_COST_USD` 护栏在真正超支前就误杀会话（§9.8）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from agent_service.models.capabilities import (
    Pricing,
    TokenPrices,
)
from agent_service.models.catalog import ModelEntry, get_entry
from agent_service.observability.cost import cost_usd, to_token_usage
from agent_service.observability.event_bus import EventBus
from agent_service.observability.prompts import PromptFingerprint
from agent_service.orchestrator.state import AgentRun, ResearchState
from agent_service.schemas.common import AgentName, Stage, TaskStatus
from agent_service.schemas.events import (
    AgentRunMetricsEvent,
    ErrorInfo,
    EventType,
    ResearchEvent,
    StageChangedPayload,
    TokenUsage,
)


def _payload[T](event: ResearchEvent, payload_type: type[T]) -> T:
    payload = event.payload
    assert isinstance(payload, payload_type)
    return payload


_AT = datetime(2026, 9, 7, 3, tzinfo=UTC)
"""北京时间 11:00，落在 DeepSeek 高峰时段。"""


@dataclass
class _Details:
    cached_tokens: int


@dataclass
class _Usage:
    """够用的 SDK `Usage` 替身。"""

    input_tokens: int = 0
    output_tokens: int = 0
    input_tokens_details: _Details | None = None


def _state(clock: Callable[[], float] = lambda: 0.0) -> ResearchState:
    bus = EventBus("sess-1", heartbeat_interval_s=60.0)
    return ResearchState("sess-1", "问题", bus=bus, clock=clock)


async def _drain(bus: EventBus) -> list[ResearchEvent]:
    bus.close()
    return [event async for event in bus.stream()]


def _pricing_entry(pricing: Pricing | None) -> ModelEntry:
    """复制一个真实条目并替换定价，避免手搓一整个 ModelEntry。"""
    base = get_entry("deepseek:deepseek-v4-pro")
    return base.model_copy(
        update={"capabilities": base.capabilities.model_copy(update={"pricing": pricing})}
    )


def _run(entry: ModelEntry, usage: _Usage | None = None, **overrides: object) -> AgentRun:
    return AgentRun(
        agent=AgentName.RESEARCH_MANAGER,
        model=entry,
        duration_ms=100,
        usage=usage or _Usage(),
        **overrides,  # pyright: ignore[reportArgumentType]
    )


# ── usage 提取 ───────────────────────────────────────────────────────────────


def test_cached_tokens_are_extracted() -> None:
    usage = to_token_usage(
        _Usage(input_tokens=2417, output_tokens=2075, input_tokens_details=_Details(2304))
    )

    assert usage == TokenUsage(input=2417, output=2075, cached=2304)


def test_missing_details_degrade_to_zero_cache() -> None:
    """不是所有 provider 都返回 input_tokens_details。

    取不到时按 0 算——宁可高估成本，也不要让护栏形同虚设。
    """
    usage = to_token_usage(_Usage(input_tokens=100, output_tokens=50))

    assert usage.cached == 0


def test_cached_never_exceeds_input() -> None:
    """防御上游给出不自洽的数字：cached > input 会让未命中量变成负数。"""
    usage = to_token_usage(
        _Usage(input_tokens=10, output_tokens=5, input_tokens_details=_Details(999))
    )

    assert usage.cached == 10


# ── 成本 ─────────────────────────────────────────────────────────────────────


def test_cache_hits_are_priced_separately() -> None:
    """v4-pro 高峰：未命中 $1.32/M，命中 $0.044/M，输出 $3.96/M。"""
    entry = _pricing_entry(Pricing(peak=TokenPrices(input=1.32, output=3.96, cached_input=0.044)))
    usage = TokenUsage(input=1000, output=1000, cached=900)

    cost = cost_usd(entry, usage, _AT)

    expected = (100 * 1.32 + 900 * 0.044 + 1000 * 3.96) / 1_000_000
    assert cost == pytest.approx(expected)


def test_ignoring_the_cache_split_would_overstate_cost() -> None:
    """这个对比就是分开计价的理由。

    v4-pro 的缓存价差是 30 倍，但那是 100% 命中时的上限；planner 实测的
    96% 命中率下高估约 14 倍。即便如此，$1 的护栏也会在实际花掉 $0.07 时
    就把会话拦死。
    """
    entry = _pricing_entry(Pricing(peak=TokenPrices(input=1.32, output=0.0, cached_input=0.044)))
    usage = TokenUsage(input=10_000, output=0, cached=9_600)

    correct = cost_usd(entry, usage, _AT)
    naive = cost_usd(entry, TokenUsage(input=10_000, output=0, cached=0), _AT)

    assert correct is not None
    assert naive is not None
    assert naive / correct == pytest.approx(13.9, abs=0.1)


def test_unknown_pricing_returns_none_not_zero() -> None:
    """把未知成本当免费会让护栏静默失效，且前端会显示一个错误的 $0.00。"""
    assert cost_usd(_pricing_entry(None), TokenUsage(input=1000), _AT) is None


# ── 状态 ─────────────────────────────────────────────────────────────────────


async def test_advance_to_emits_stage_changed_with_previous() -> None:
    """阶段迁移与事件绑在一起：分开做就一定会漂移，而漂移在日志里看不出来。"""
    state = _state()

    state.advance_to(Stage.PLANNING)
    state.advance_to(Stage.RESEARCHING)
    events = await _drain(state.bus)

    assert [event.type for event in events] == [
        EventType.STAGE_CHANGED,
        EventType.STAGE_CHANGED,
    ]
    payloads = [_payload(event, StageChangedPayload) for event in events]
    assert [payload.stage for payload in payloads] == [Stage.PLANNING, Stage.RESEARCHING]
    assert payloads[0].previous is None
    assert payloads[1].previous is Stage.PLANNING


async def test_stage_change_carries_a_user_facing_message() -> None:
    """§12.3：事件要自带能直接显示的文案，前端不该再维护一份阶段→中文的映射。"""
    state = _state()
    state.advance_to(Stage.RESEARCHING)

    assert (await _drain(state.bus))[0].message == "正在执行研究任务"


def test_usage_accumulates_across_runs() -> None:
    state = _state()
    entry = _pricing_entry(Pricing(peak=TokenPrices(input=1.0, output=1.0)))

    state.record_run(_run(entry, _Usage(input_tokens=100, output_tokens=10)), at=_AT)
    state.record_run(_run(entry, _Usage(input_tokens=200, output_tokens=20)), at=_AT)

    assert state.usage == TokenUsage(input=300, output=30, cached=0)
    assert state.cost_usd == pytest.approx(330 / 1_000_000)


async def test_record_run_emits_a_metrics_event() -> None:
    """记账与发事件必须绑在一起（§20.1）。

    `agent_runs` 是 `/debug` 与跨模型 eval 的唯一数据源，而 Python 不碰业务库
    （决策 C），这行数据只能靠事件流过去。留一个"只记账不发事件"的口子，
    就一定会有某条路径忘记发，且不会有任何报错。
    """
    state = _state()
    entry = _pricing_entry(Pricing(peak=TokenPrices(input=1.0, output=1.0)))

    state.record_run(
        _run(
            entry,
            _Usage(input_tokens=100, output_tokens=10),
            task_id="t1",
            prompt=PromptFingerprint(hash="abc123", chars=4096),
        ),
        at=_AT,
    )

    (event,) = await _drain(state.bus)
    assert isinstance(event, AgentRunMetricsEvent)
    assert event.payload.model_id == entry.id
    assert event.payload.task_id == "t1"
    assert event.payload.status is TaskStatus.COMPLETED
    assert event.payload.usage == TokenUsage(input=100, output=10)
    assert event.payload.cost_usd == pytest.approx(110 / 1_000_000)
    assert event.payload.prompt is not None
    assert event.payload.prompt.hash == "abc123"
    assert event.payload.prompt.chars == 4096


async def test_failed_run_is_recorded_with_its_error() -> None:
    state = _state()

    state.record_run(
        _run(
            _pricing_entry(Pricing(peak=TokenPrices(input=1.0, output=1.0))),
            _Usage(input_tokens=100),
            error=ErrorInfo(code="structured_output", message="重试用尽"),
        ),
        at=_AT,
    )

    (event,) = await _drain(state.bus)
    assert isinstance(event, AgentRunMetricsEvent)
    assert event.payload.status is TaskStatus.FAILED
    assert event.payload.error is not None
    assert event.payload.error.code == "structured_output"
    # 失败照样计费
    assert event.payload.cost_usd == pytest.approx(100 / 1_000_000)


async def test_metrics_event_never_carries_the_prompt_text() -> None:
    """§20.3：日志与埋点里禁止出现完整 prompt。"""
    state = _state()
    secret = "你是一个金融研究规划器" * 100

    state.record_run(
        _run(_pricing_entry(None), prompt=PromptFingerprint(hash="h", chars=len(secret))),
        at=_AT,
    )

    (event,) = await _drain(state.bus)
    assert secret not in event.model_dump_json()


def test_cost_stays_none_when_pricing_is_unknown() -> None:
    state = _state()

    state.record_run(_run(_pricing_entry(None), _Usage(input_tokens=100)), at=_AT)

    assert state.usage.input == 100  # 用量照记，只是算不出钱
    assert state.cost_usd is None


def test_unknown_pricing_does_not_trip_the_budget_guard() -> None:
    """宁可放行也不要因为目录缺一个价格就把会话拦死。"""
    state = _state()
    state.record_run(_run(_pricing_entry(None), _Usage(input_tokens=10**9)), at=_AT)

    assert not state.over_budget(0.01)


def test_over_budget_triggers_at_the_limit() -> None:
    state = _state()
    state.cost_usd = 1.0

    assert state.over_budget(1.0)
    assert not state.over_budget(1.01)


def test_failed_and_skipped_tasks_are_both_reported_as_unusable() -> None:
    """依赖方只关心「上游有没有产出」，failed 与 skipped 在这一点上等价。"""
    state = _state()
    state.task_status = {
        "t1": TaskStatus.COMPLETED,
        "t2": TaskStatus.FAILED,
        "t3": TaskStatus.SKIPPED,
        "t4": TaskStatus.PENDING,
    }

    assert state.failed_task_ids() == {"t2", "t3"}


def test_remaining_s_shrinks_as_time_passes() -> None:
    """执行器用它把单任务超时压到剩余预算内，否则最后一个任务能吃掉报告的时间。"""
    now = [0.0]
    state = _state(clock=lambda: now[0])

    now[0] = 30.0

    assert state.remaining_s(100.0) == pytest.approx(70.0)
    assert state.elapsed_ms == 30_000
