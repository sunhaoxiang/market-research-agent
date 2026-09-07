"""全局测试夹具。"""

from __future__ import annotations

import pytest
from agents import set_tracing_disabled


@pytest.fixture(autouse=True)
def _no_trace_upload() -> None:
    """测试期禁用 tracing 上传。

    不关的话每次 `Runner.run` 都会尝试把 trace 发到 OpenAI：CI 里没有 key 会
    刷一片 401，本地有 key 则是白花网络往返、还把测试数据混进真实 trace 面板。
    需要看 trace 时用真实模型手动跑（P1-9 起），而不是在单测里。

    刻意用函数级而非 session 级：`test_registry` 里验证 `bootstrap_sdk` 的用例
    会调用 `set_tracing_disabled(False)`，session 级只在开头设一次会被它推翻，
    之后所有跑 `Runner.run` 的测试都会重新开始上传。
    """
    set_tracing_disabled(True)
