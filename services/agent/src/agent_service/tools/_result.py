"""把 ProviderError 收成 ToolResult——这一层才面对 LLM / invoke 调用方。"""

from __future__ import annotations

from pydantic import ValidationError

from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import ToolError, ToolErrorCode, ToolResult


def fail_provider[T](tool: str, error: ProviderError) -> ToolResult[T]:
    return ToolResult.failure(error.to_tool_error(tool=tool))


def fail_unavailable[T](*, tool: str, provider: str, message: str) -> ToolResult[T]:
    return ToolResult.failure(
        ToolError(
            code=ToolErrorCode.UPSTREAM_ERROR,
            message=message,
            tool=tool,
            provider=provider,
            retryable=False,
        )
    )


def fail_invalid[T](tool: str, message: str) -> ToolResult[T]:
    return ToolResult.failure(
        ToolError(
            code=ToolErrorCode.INVALID_INPUT,
            message=message,
            tool=tool,
            retryable=False,
        )
    )


def fail_unsupported[T](tool: str, message: str, *, provider: str | None = None) -> ToolResult[T]:
    return ToolResult.failure(
        ToolError(
            code=ToolErrorCode.UNSUPPORTED,
            message=message,
            tool=tool,
            provider=provider,
            retryable=False,
        )
    )


def fail_validation[T](tool: str, error: ValidationError) -> ToolResult[T]:
    first = error.errors()[0]
    loc = ".".join(str(part) for part in first["loc"] if part != "body")
    message = first["msg"]
    if loc:
        message = f"{loc}: {message}"
    return fail_invalid(tool, message)
