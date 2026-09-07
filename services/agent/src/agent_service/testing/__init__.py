"""测试替身。

放在 `src/` 而非 `tests/` 下是有意的：eval 脚本（Phase 5）与 `scripts/` 里的
本地调试工具也要用 FakeModel，它们不在 pytest 的导入路径里。
"""

from agent_service.testing.fake_model import (
    FakeModel,
    FakeTurn,
    message,
    tool_call,
)

__all__ = ["FakeModel", "FakeTurn", "message", "tool_call"]
