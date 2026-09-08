"""调试专用：直接调用单个 tool（§16.2）。

脱离 LLM 测 tool 是 Phase 2 能落地的前提——搜/抓的契约错误不该
等 Agent 跑一遍才暴露。错误走 ToolResult（200 + ok=false），
「这个名字根本不是 tool」才是 404：前者是工具语义，后者是路由语义。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from agent_service.api.auth import require_internal_token
from agent_service.schemas.tools import DataProvenance, DataQuality, ToolError
from agent_service.tools.deps import ToolDeps
from agent_service.tools.registry import HANDLERS, invoke_tool

router = APIRouter(prefix="/v1/tools", tags=["tools"])


class ToolInvokeRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolInvokeResponse(BaseModel):
    ok: bool
    data: dict[str, Any] | None = None
    error: ToolError | None = None
    provenance: DataProvenance | None = None
    quality: DataQuality | None = None


def _deps(request: Request) -> ToolDeps:
    runtime = getattr(request.app.state, "provider_runtime", None)
    return ToolDeps(
        search=getattr(request.app.state, "search_provider", None),
        fetcher=getattr(request.app.state, "web_fetcher", None),
        coingecko=getattr(request.app.state, "coingecko", None),
        defillama=getattr(request.app.state, "defillama", None),
        hyperliquid=getattr(request.app.state, "hyperliquid", None),
        sec_edgar=getattr(request.app.state, "sec_edgar", None),
        fmp=getattr(request.app.state, "fmp", None),
        clock=None if runtime is None else runtime.clock,
    )


@router.post(
    "/{name}/invoke",
    response_model=ToolInvokeResponse,
    dependencies=[Depends(require_internal_token)],
)
async def invoke(name: str, body: ToolInvokeRequest, request: Request) -> ToolInvokeResponse:
    if name not in HANDLERS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "TOOL_NOT_FOUND",
                    "message": f"未知工具：{name}",
                }
            },
        )
    result = await invoke_tool(name, body.arguments, _deps(request))
    dumped = result.model_dump(mode="json")
    data = dumped.get("data")
    if data is not None and not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": "INTERNAL", "message": "tool 返回了非对象 data"}},
        )
    return ToolInvokeResponse(
        ok=result.ok,
        data=data,
        error=result.error,
        provenance=result.provenance,
        quality=result.quality,
    )
