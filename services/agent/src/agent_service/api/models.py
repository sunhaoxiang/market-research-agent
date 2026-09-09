"""模型目录端点（P1-5 / §9.3）。

前端据此渲染 Provider → Model 两级选择器。**没配 key 的模型返回 `available=false`
并附上原因**，而不是让用户选完之后才在调用时报错。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from agent_service.models import ModelCapabilities
from agent_service.models.catalog import CATALOG
from agent_service.models.registry import ModelRegistry
from agent_service.schemas.common import ModelRole

router = APIRouter(prefix="/v1", tags=["models"])


class ModelInfo(BaseModel):
    id: str
    provider: str
    display_name: str
    available: bool
    unavailable_reason: str | None = None
    capabilities: ModelCapabilities
    verified_at: str | None = Field(
        default=None, description="参数最后一次对照官方文档核实的日期；null 表示未核实"
    )
    verified: bool = Field(
        description=(
            "是否完成过一次完整研究冒烟（含 §9.4 结构化输出路径）。与 verified_at 不是同一件事。"
        )
    )
    notes: str | None = None


class LimitsInfo(BaseModel):
    """当前进程的执行上限（环境变量，尚未叠用户设置）。"""

    max_tasks_per_plan: int
    max_parallel_tasks: int
    max_tool_calls_per_agent: int
    max_supplement_rounds: int
    task_timeout_s: float
    total_timeout_s: float
    max_session_cost_usd: float


class ModelsResponse(BaseModel):
    models: list[ModelInfo]
    role_defaults: dict[str, str] = Field(
        description="角色 → 当前生效的 model_id，已应用 MODEL_ROLE_* 环境变量覆盖"
    )
    default_model_id: str
    limits: LimitsInfo


def _registry(request: Request) -> ModelRegistry:
    return request.app.state.registry


@router.get("/models", response_model=ModelsResponse)
async def list_models(request: Request) -> ModelsResponse:
    registry = _registry(request)
    reasons = registry.unavailable_reasons()

    models = [
        ModelInfo(
            id=entry.id,
            provider=entry.provider.value,
            display_name=entry.display_name,
            available=entry.id not in reasons,
            unavailable_reason=reasons.get(entry.id),
            capabilities=entry.capabilities,
            verified_at=entry.verified_at.isoformat() if entry.verified_at else None,
            verified=entry.verified,
            notes=entry.notes,
        )
        for entry in CATALOG
    ]

    return ModelsResponse(
        models=models,
        role_defaults={role.value: registry.model_id_for_role(role) for role in ModelRole},
        default_model_id=registry.settings.default_model_id,
        limits=LimitsInfo.model_validate(registry.settings.limits.model_dump()),
    )
