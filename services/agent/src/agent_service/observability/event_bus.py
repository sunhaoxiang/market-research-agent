"""会话事件总线（§12，P1-6）。

编排层往里 `emit`，SSE 端点从 `stream()` 里读。三个设计取舍值得说明：

**`emit` 是同步方法，队列不设上限。** 这两点是同一个决定的两面，
且互为前提：`put_nowait` 永不挂起，于是 seq 分配与入队之间没有 await 点，
「seq 顺序 == 出队顺序」由构造保证，而不是靠锁去补救。

反面已实测：改成「有界队列 + async emit」后，队列打满时部分生产者会阻塞在
`put()` 上，恢复顺序不再等于 seq 分配顺序，实测出现 50→11 这种倒退。
`asyncio` 对均匀挂起（如 `sleep(0)`）是 FIFO 重排的，看不出问题——
必须有慢消费者把队列打满才会暴露，属于压测才碰得到、线上才出事的那类 bug。

不设上限的代价是内存，但生产侧事件量已被 §7.2 的执行上限约束
（≤6 任务、每 agent ≤12 次工具调用），量级在数千条、几 MB 以内。
反过来给队列设上限意味着**慢客户端会反压并卡住研究流程**——
用户浏览器卡一下不该让 agent 停工。超过高水位只记 warning，不阻塞。

**心跳用读超时实现，而非后台任务。** `wait_for(queue.get(), timeout)`
超时即产出心跳，天然只在空闲时触发，也不需要管理任务生命周期与取消。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from agent_service.schemas.events import (
    TERMINAL_EVENT_TYPES,
    HeartbeatEvent,
    ResearchEvent,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

log = structlog.get_logger(__name__)

DEFAULT_HEARTBEAT_INTERVAL_S = 15.0
"""心跳间隔。需明显小于反向代理与浏览器的空闲超时（常见 30-60s）。"""

_HIGH_WATER_MARK = 2_000
"""积压告警阈值。触发通常意味着消费端断了或卡了，而非事件真的这么多。"""


class BusClosedError(RuntimeError):
    """向已关闭的总线 emit。"""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"会话 {session_id} 的事件总线已关闭，不能再发事件")
        self.session_id = session_id


class _Sentinel:
    """流终止标记。

    用哨兵而不是「补发一个终止事件」：`close()` 是异常路径的安全网
    （编排层崩溃、没能发出 session_failed），此时不该伪造业务事件。
    """


_CLOSE = _Sentinel()


class EventBus:
    """单会话、单消费者的事件通道。"""

    def __init__(
        self,
        session_id: str,
        *,
        heartbeat_interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S,
    ) -> None:
        self.session_id = session_id
        self.heartbeat_interval_s = heartbeat_interval_s
        # maxsize=0 即无上限：见模块 docstring 中关于反压的说明
        self._queue: asyncio.Queue[ResearchEvent | _Sentinel] = asyncio.Queue()
        self._seq = 0
        self._closed = False
        self._warned_backlog = False

    @property
    def last_seq(self) -> int:
        """已分配的最大 seq。断线重连时与 `Last-Event-ID` 比较。"""
        return self._seq

    @property
    def closed(self) -> bool:
        return self._closed

    def emit[E: ResearchEvent](self, event_type: type[E], **fields: Any) -> E:
        """构造并投递一个事件，返回构造好的实例（便于埋点与断言）。

        泛型上界刻意是 `ResearchEvent`（判别联合）而不是 `EventEnvelope`：
        后者会让任何自定义信封子类都能塞进队列，而消费端按联合类型反序列化，
        运行期才会炸。用上界把「只能发协议内已定义的事件」变成编译期约束。

        `seq` 与 `session_id` 由总线填充——让调用方自己传 seq 就等于把
        单调性的保证分散到每个调用点，迟早有人传错。

        同步方法，理由见模块 docstring。
        """
        if self._closed:
            raise BusClosedError(self.session_id)

        self._seq += 1
        event = event_type(seq=self._seq, session_id=self.session_id, **fields)
        self._queue.put_nowait(event)
        self._check_backlog()
        return event

    def close(self) -> None:
        """终止流。幂等。

        正常路径不需要调用——编排层发出终止事件后 `stream()` 会自行结束。
        这是异常路径的安全网：编排层崩溃时若不关闭，消费者会永远收心跳。
        """
        if self._closed:
            return
        self._closed = True
        self._queue.put_nowait(_CLOSE)

    async def stream(self) -> AsyncGenerator[ResearchEvent]:
        """产出事件直到终止事件或 `close()`。空闲时插入心跳。

        只支持单消费者：每个会话对应一条 SSE 连接。
        """
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=self.heartbeat_interval_s)
            except TimeoutError:
                yield self._heartbeat()
                continue

            if isinstance(item, _Sentinel):
                return

            yield item

            if item.type in TERMINAL_EVENT_TYPES:
                # 终止事件之后不应再有事件；标记关闭让后续 emit 立刻报错，
                # 而不是静默丢进一个再也不会被消费的队列
                self._closed = True
                return

    def _heartbeat(self) -> HeartbeatEvent:
        """心跳同样占用 seq，保持「seq 连续」这一不变量。

        留空洞会让前端无法区分「心跳」与「漏收了事件」。
        """
        self._seq += 1
        return HeartbeatEvent(seq=self._seq, session_id=self.session_id)

    def _check_backlog(self) -> None:
        backlog = self._queue.qsize()
        if backlog > _HIGH_WATER_MARK and not self._warned_backlog:
            self._warned_backlog = True
            log.warning(
                "event_bus.backlog",
                session_id=self.session_id,
                backlog=backlog,
                hint="消费端可能已断开或阻塞；事件仍在累积，不做反压",
            )
