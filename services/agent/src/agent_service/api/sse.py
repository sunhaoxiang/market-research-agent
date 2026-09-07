"""SSE 帧编码（§12.3，P1-11）。

单独成一个模块是为了让分帧规则可以脱离 FastAPI 单测——帧格式错了的表现是
前端静默收不到事件，而不是报错，这类 bug 靠端到端测试很难定位。

用 SSE 而不是 WebSocket：事件流是**单向**的（服务端 → 客户端），
SSE 走普通 HTTP，能直接被 Next.js 的 Route Handler 当作 `fetch` 的响应体转发，
不需要在 BFF 层再维护一条独立的 WebSocket 连接。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import orjson

if TYPE_CHECKING:
    from agent_service.schemas.events import ResearchEvent

EVENT_NAME = "research"
"""所有事件共用一个 SSE `event:` 名。

区分事件类型的是 payload 里的 `type` 字段（§12.1 的判别联合），
不是 SSE 的 event 名——否则前端得为每种类型注册一个 listener，
新增事件类型时旧前端会直接漏掉它，而不是走 `message` 兜底显示。
"""

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # 关掉 nginx / Next.js 反代的响应缓冲。不加这个头，事件会被攒到
    # 缓冲区满或连接结束才一次性下发，"实时"就没了
    "X-Accel-Buffering": "no",
}


def encode_event(event: ResearchEvent) -> bytes:
    """把事件编码成一个 SSE 帧。

    `id:` 用 seq，让浏览器断线重连时能通过 `Last-Event-ID` 请求头带回
    最后收到的位置（§12.1 的顺序保证与重连基础，回放在 P6-6 实现）。

    JSON 保证单行：`orjson.dumps` 不会输出裸换行，因此不必处理多行 data 的
    拆分。但解析端仍需支持多行——SSE 规范允许，别的实现可能会用。
    """
    data = orjson.dumps(event.model_dump(mode="json"))
    return b"id: %d\nevent: %s\ndata: %s\n\n" % (event.seq, EVENT_NAME.encode(), data)


def encode_comment(text: str) -> bytes:
    """SSE 注释帧（以 `:` 开头），客户端会忽略。

    用途是在流刚建立、第一个业务事件还没产生时就把响应头推出去：
    有些代理要等到收到第一个字节才认为响应已开始。
    """
    return f": {text}\n\n".encode()
