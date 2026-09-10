"""P5-9 多模型冒烟状态。

CI 和默认 pytest **不打真实 LLM**。本模块根据 catalog 的 `verified` 字段
与当前已配置的 API key，生成一份确定性的按-provider 报告。

验收口径：已配 key 的 provider 全部跑通，或明确标注限制。
本机目前只有 DeepSeek key——其余五家记为 `skipped_no_key`，而不是假装跑过。
配了 key 但 catalog 仍全是 `verified=False` 的记为 `pending`（例如以后配了
智谱、还没做完整研究冒烟）。
"""

from __future__ import annotations

from enum import StrEnum

from agent_service.config import Settings
from agent_service.models.capabilities import ProviderId, StructuredOutputMode
from agent_service.models.catalog import list_entries
from agent_service.models.registry import ModelRegistry
from agent_service.schemas.common import Schema


class SmokeStatus(StrEnum):
    VERIFIED = "verified"
    SKIPPED_NO_KEY = "skipped_no_key"
    PENDING = "pending"


class ProviderSmokeReport(Schema):
    provider: ProviderId
    status: SmokeStatus
    structured_output: list[StructuredOutputMode]
    model_ids: list[str]
    verified_model_ids: list[str]
    note: str


def _env_var(provider: ProviderId) -> str:
    return f"{provider.value.upper()}_API_KEY"


def _verified_note(provider: ProviderId) -> str:
    if provider is ProviderId.DEEPSEEK:
        return (
            "json_mode 完整研究已在 Phase 2–4 真跑通过。"
            "deepseek-flash（V4.1 Flash）现为四角色默认，含 planner。"
        )
    return "完整研究冒烟已通过。"


def provider_smoke_reports(
    settings: Settings | None = None,
) -> tuple[ProviderSmokeReport, ...]:
    """按 catalog 里出现过的 provider 各出一条报告，不打 LLM。"""
    registry = ModelRegistry(settings)
    reports: list[ProviderSmokeReport] = []
    for provider in ProviderId:
        entries = list_entries(provider)
        if not entries:
            continue
        has_key = registry.credential(provider) is not None
        verified_ids = [entry.id for entry in entries if entry.verified]
        modes = list(dict.fromkeys(entry.capabilities.structured_output for entry in entries))
        env_var = _env_var(provider)
        if not has_key:
            status = SmokeStatus.SKIPPED_NO_KEY
            note = f"未配置 {env_var}，完整研究冒烟未跑。"
        elif verified_ids:
            status = SmokeStatus.VERIFIED
            note = _verified_note(provider)
        else:
            status = SmokeStatus.PENDING
            note = f"已配置 {env_var}，但尚未完成完整研究冒烟。"
        reports.append(
            ProviderSmokeReport(
                provider=provider,
                status=status,
                structured_output=modes,
                model_ids=[entry.id for entry in entries],
                verified_model_ids=verified_ids,
                note=note,
            )
        )
    return tuple(reports)
