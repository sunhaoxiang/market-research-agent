"""structlog 配置。

约定（DEVELOPMENT_PLAN.md §20.3）：
- 输出 JSON，便于后续接入日志系统
- 每条日志通过 contextvars 自动携带 session_id / task_id / agent / tool
- **禁止**记录 API key 与完整 prompt（prompt 只记 hash 与长度）
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "token",
        "secret",
        "password",
        "internal_api_token",
        "prompt",
        "messages",
    }
)


def _redact_sensitive(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """兜底脱敏：即使调用方不小心传了敏感字段，也不会落到日志里。"""
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging(level: str = "info", *, json_output: bool = True) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
        force=True,
    )

    renderer: structlog.types.Processor = (
        # ensure_ascii=False：否则中文会被转义成 \uXXXX，日志无法直接阅读
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _redact_sensitive,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
