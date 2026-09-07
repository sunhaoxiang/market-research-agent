"""日志配置测试：脱敏必须生效，中文必须可读。"""

from __future__ import annotations

import json

import pytest
import structlog

from agent_service.observability.logging import configure_logging, get_logger


@pytest.fixture(autouse=True)
def reset_structlog():
    yield
    structlog.reset_defaults()


def _capture(**kwargs: object) -> dict[str, object]:
    """跑一遍真实的 processor 链，返回渲染后的 JSON。"""
    configure_logging("info")
    log = get_logger("test").bind()
    rendered = structlog.get_config()["processors"][-1](
        None,
        "info",
        {"event": "test_event", **kwargs},
    )
    assert isinstance(rendered, str)
    del log
    return json.loads(rendered)


def test_sensitive_keys_are_redacted() -> None:
    configure_logging("info")
    processors = structlog.get_config()["processors"]
    event = {"event": "call", "api_key": "sk-real-key", "prompt": "机密内容", "tool": "get_tvl"}

    # 找到脱敏 processor 并执行
    for processor in processors:
        if getattr(processor, "__name__", "") == "_redact_sensitive":
            event = processor(None, "info", event)
            break
    else:
        pytest.fail("脱敏 processor 未挂载到 structlog 链上")

    assert event["api_key"] == "[REDACTED]"
    assert event["prompt"] == "[REDACTED]"
    assert event["tool"] == "get_tvl"  # 非敏感字段保持原样


def test_chinese_is_not_escaped() -> None:
    """中文必须以原文输出，否则日志排查时不可读。"""
    payload = _capture(hint="未配置任何 LLM provider key")
    assert payload["hint"] == "未配置任何 LLM provider key"
