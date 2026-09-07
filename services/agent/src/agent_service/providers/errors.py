"""Provider 层错误。

这里抛异常、不返回 `ToolResult`：重试循环用异常控制流更干净。
Tool 层（P2-4）负责 catch 并转成 `ok=False` 的结构化返回值——那一层才面对 LLM，
栈信息绝不能漏出去（§8.1）。
"""

from __future__ import annotations

from agent_service.schemas.tools import ToolError, ToolErrorCode


class ProviderError(Exception):
    """一次外部调用的最终失败。不再被 BaseProvider 重试。"""

    def __init__(
        self,
        code: ToolErrorCode,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        provider: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.provider = provider
        self.endpoint = endpoint

    def to_tool_error(self, *, tool: str | None = None) -> ToolError:
        return ToolError(
            code=self.code,
            message=self.message,
            tool=tool,
            provider=self.provider,
            retryable=self.retryable,
        )


class RetryableProviderError(ProviderError):
    """会被 tenacity 吃掉并重试的瞬时失败：429 / 5xx / 超时 / 网络错误。"""

    def __init__(
        self,
        code: ToolErrorCode,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_s: float | None = None,
        provider: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        super().__init__(
            code,
            message,
            retryable=True,
            status_code=status_code,
            provider=provider,
            endpoint=endpoint,
        )
        self.retry_after_s = retry_after_s
