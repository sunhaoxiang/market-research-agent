"""Token 用量与成本核算（§20.1）。

SDK 的 `Usage` 与本协议的 `TokenUsage` 分开是必要的：前者的 `input_tokens`
**包含**缓存命中部分，而缓存价可低至未命中的 3%（§9.8）。混在一起算会让
`MAX_SESSION_COST_USD` 护栏严重失真——按 planner 实测的 96% 命中率，
输入成本会被高估约 14 倍，$1 的上限在实际花掉 $0.07 时就把会话拦死。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from agent_service.schemas.events import TokenUsage

if TYPE_CHECKING:
    from datetime import datetime

    from agent_service.models.catalog import ModelEntry

log = structlog.get_logger(__name__)


def to_token_usage(sdk_usage: object) -> TokenUsage:
    """从 SDK 的 `Usage` 提取用量。

    刻意用 `getattr` 而不是直接取属性：`input_tokens_details` 并非所有
    provider 都返回，而成本核算不该因为某个 provider 少给一个字段就崩掉。
    取不到缓存量时按 0 算——宁可高估成本，也不要让护栏形同虚设。
    """
    total_input = _int(sdk_usage, "input_tokens")
    cached = _int(getattr(sdk_usage, "input_tokens_details", None), "cached_tokens")
    return TokenUsage(
        input=total_input,
        output=_int(sdk_usage, "output_tokens"),
        cached=min(cached, total_input),
    )


def cost_usd(entry: ModelEntry, usage: TokenUsage, at: datetime) -> float | None:
    """按分时价与缓存命中计算成本。定价未知时返回 None 并记 warning。

    返回 `None` 而不是 0.0：把未知成本当免费会让成本护栏静默失效，
    而 None 能一路传到 `SessionCompletedPayload.cost_usd`，前端可以显示
    「成本未知」而不是一个错误的 $0.00。
    """
    pricing = entry.capabilities.pricing
    if pricing is None:
        log.warning("cost.pricing_unknown", model_id=entry.id)
        return None

    return pricing.cost_usd(
        input_tokens=usage.input - usage.cached,
        output_tokens=usage.output,
        cached_tokens=usage.cached,
        at=at,
    )


def _int(source: object, name: str) -> int:
    value = getattr(source, name, 0)
    return value if isinstance(value, int) else 0
