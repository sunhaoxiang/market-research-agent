"""P1-6 事件总线测试。

验收标准是「并发发事件时 seq 严格递增」——这条不只是断言数字，
更是在锁住 `emit` 保持同步的设计（见 event_bus 模块 docstring）。
"""

from __future__ import annotations

import asyncio

import pytest

from agent_service.observability.event_bus import (
    BusClosedError,
    EventBus,
)
from agent_service.schemas.common import Stage
from agent_service.schemas.events import (
    EventType,
    SessionCancelledEvent,
    SessionCompletedEvent,
    SessionCompletedPayload,
    StageChangedEvent,
    StageChangedPayload,
    TokenUsage,
    WarningEvent,
    WarningPayload,
)


def _bus(**kwargs: float) -> EventBus:
    return EventBus("sess-1", **kwargs)  # pyright: ignore[reportArgumentType]


def _stage(stage: Stage = Stage.RESEARCHING) -> dict[str, object]:
    return {"payload": StageChangedPayload(stage=stage)}


def _completed() -> dict[str, object]:
    return {"payload": SessionCompletedPayload(duration_ms=1, usage=TokenUsage())}


# ─── seq 分配 ────────────────────────────────────────────────────────────────


def test_seq_starts_at_one_and_increments() -> None:
    bus = _bus()

    first = bus.emit(StageChangedEvent, **_stage(Stage.PLANNING))
    second = bus.emit(StageChangedEvent, **_stage(Stage.RESEARCHING))

    assert (first.seq, second.seq) == (1, 2)
    assert bus.last_seq == 2


def test_bus_fills_in_session_id() -> None:
    """让调用方自己传 session_id/seq 就是把不变量分散到每个调用点。"""
    event = _bus().emit(StageChangedEvent, **_stage())
    assert event.session_id == "sess-1"


async def test_seq_is_strictly_increasing_under_concurrency() -> None:
    """P1-6 验收标准。

    真正被验证的是 `emit` 的同步性：seq 分配与入队之间没有 await 点，
    所以「seq 顺序 == 出队顺序」由构造保证。若哪天有人把 emit 改成
    async 并在入队处引入挂起，这个测试会立刻发现乱序。
    """
    bus = _bus()
    emitted_count = 200

    async def emitter(index: int) -> None:
        # 让出控制权，尽量把并发交错放大
        await asyncio.sleep(0)
        bus.emit(WarningEvent, payload=WarningPayload(code="c", message=f"#{index}"))

    await asyncio.gather(*(emitter(i) for i in range(emitted_count)))
    bus.emit(SessionCancelledEvent)

    seqs = [event.seq async for event in bus.stream()]

    assert seqs == sorted(seqs), "出队顺序与 seq 顺序不一致"
    assert seqs == list(range(1, emitted_count + 2))


async def test_slow_consumer_does_not_reorder_events() -> None:
    """慢消费者是 seq 乱序的真正触发条件。

    实测过反面实现：换成「有界队列 + async emit」后，队列打满时部分生产者
    阻塞在 `put()` 上，恢复顺序不再等于 seq 分配顺序，会出现 50→11 这种倒退。
    均匀挂起（`sleep(0)`）看不出问题，必须有慢消费者才暴露——所以这个测试
    刻意让消费端比生产端慢。
    """
    bus = _bus()
    total = 120

    async def produce() -> None:
        for index in range(total):
            await asyncio.sleep(0)
            bus.emit(WarningEvent, payload=WarningPayload(code="c", message=f"#{index}"))
        bus.emit(SessionCancelledEvent)

    received: list[int] = []

    async def consume() -> None:
        async for event in bus.stream():
            received.append(event.seq)
            await asyncio.sleep(0.0005)  # 刻意比生产端慢

    await asyncio.gather(produce(), consume())

    assert received == sorted(received)
    assert received == list(range(1, total + 2))


async def test_no_seq_is_reused_across_event_kinds() -> None:
    bus = _bus()
    seqs = {
        bus.emit(StageChangedEvent, **_stage()).seq,
        bus.emit(WarningEvent, payload=WarningPayload(code="c", message="d")).seq,
        bus.emit(SessionCancelledEvent).seq,
    }
    assert len(seqs) == 3


# ─── 流 ──────────────────────────────────────────────────────────────────────


async def test_stream_yields_in_order_then_ends_on_terminal_event() -> None:
    bus = _bus()
    bus.emit(StageChangedEvent, **_stage(Stage.PLANNING))
    bus.emit(StageChangedEvent, **_stage(Stage.RESEARCHING))
    bus.emit(SessionCompletedEvent, **_completed())

    received = [event async for event in bus.stream()]

    assert [e.type for e in received] == [
        EventType.STAGE_CHANGED,
        EventType.STAGE_CHANGED,
        EventType.SESSION_COMPLETED,
    ]


@pytest.mark.parametrize(
    ("event_type", "fields"),
    [
        (SessionCompletedEvent, _completed()),
        (SessionCancelledEvent, {}),
    ],
)
async def test_every_terminal_event_ends_the_stream(
    event_type: type, fields: dict[str, object]
) -> None:
    bus = _bus()
    bus.emit(event_type, **fields)

    received = [event async for event in bus.stream()]

    assert len(received) == 1


async def test_emitting_after_a_terminal_event_is_rejected() -> None:
    """终止之后再发事件说明编排层有逻辑错误，静默丢弃只会让它更难被发现。"""
    bus = _bus()
    bus.emit(SessionCancelledEvent)
    _ = [event async for event in bus.stream()]

    with pytest.raises(BusClosedError, match="sess-1"):
        bus.emit(StageChangedEvent, **_stage())


async def test_close_ends_a_stream_that_has_no_terminal_event() -> None:
    """编排层崩溃时的安全网：不关闭的话消费者会永远收心跳。"""
    bus = _bus()
    bus.emit(StageChangedEvent, **_stage())
    bus.close()

    received = [event async for event in bus.stream()]

    assert [e.type for e in received] == [EventType.STAGE_CHANGED]
    assert bus.closed is True


def test_close_is_idempotent() -> None:
    bus = _bus()
    bus.close()
    bus.close()


async def test_stream_delivers_events_emitted_while_waiting() -> None:
    """真实时序：消费者先挂在 stream 上，编排层随后才产出事件。"""
    bus = _bus()

    async def produce() -> None:
        await asyncio.sleep(0.01)
        bus.emit(StageChangedEvent, **_stage())
        bus.emit(SessionCancelledEvent)

    task = asyncio.create_task(produce())
    received = [event async for event in bus.stream()]
    await task

    assert [e.type for e in received] == [
        EventType.STAGE_CHANGED,
        EventType.SESSION_CANCELLED,
    ]


# ─── 心跳 ────────────────────────────────────────────────────────────────────


async def test_heartbeat_fires_when_idle() -> None:
    bus = _bus(heartbeat_interval_s=0.01)
    stream = bus.stream()

    first = await anext(stream)

    assert first.type is EventType.HEARTBEAT
    await stream.aclose()


async def test_heartbeat_consumes_a_seq_to_keep_the_sequence_gapless() -> None:
    """留空洞会让前端无法区分「心跳」与「漏收事件」。"""
    bus = _bus(heartbeat_interval_s=0.01)
    stream = bus.stream()

    beat = await anext(stream)
    bus.emit(StageChangedEvent, **_stage())
    following = await anext(stream)

    assert (beat.seq, following.seq) == (1, 2)
    await stream.aclose()


async def test_heartbeat_does_not_fire_while_events_flow() -> None:
    """心跳只在空闲时补发，有事件时不该插进来。"""
    bus = _bus(heartbeat_interval_s=5.0)
    bus.emit(StageChangedEvent, **_stage())
    bus.emit(SessionCancelledEvent)

    received = [event async for event in bus.stream()]

    assert not any(e.type is EventType.HEARTBEAT for e in received)
