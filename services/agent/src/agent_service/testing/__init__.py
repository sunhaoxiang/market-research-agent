"""测试替身。

放在 `src/` 而非 `tests/` 下是有意的：eval 脚本（Phase 5）与 `scripts/` 里的
本地调试工具也要用，它们不在 pytest 的导入路径里。

**脚本化模型直接用 SDK 自带的 `agents.testing.ScriptedModel`**，不再自建。
原先这里有一个手写的 `FakeModel`，它只实现了 `get_response`，而
`Runner.run_streamed` 走的是 `stream_response`——P1-7 的翻译层测试一跑就暴露了。
自建替身要跟着 SDK 内部约定走，而那些约定我们既不控制也难以验证；
`ScriptedModel` 由 SDK 维护，且额外提供调用快照、步骤级错误注入与
`assert_complete()`。
"""

from agent_service.testing.settings import (
    IsolatedExecutionLimits,
    IsolatedProviderCredentials,
    IsolatedSettings,
)

__all__ = [
    "IsolatedExecutionLimits",
    "IsolatedProviderCredentials",
    "IsolatedSettings",
]
