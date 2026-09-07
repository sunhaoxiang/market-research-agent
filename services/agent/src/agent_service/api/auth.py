"""内部服务鉴权（§11.3）。

这不是面向公网的认证。Python 服务只监听 `127.0.0.1`，威胁模型是**本机其他进程
误调或恶意调用**——它花的是真钱（LLM + 外部 API）。共享随机串足够挡住这个。

未配置 `INTERNAL_API_TOKEN` 时放行，并在启动时记一次 warning。这是为了让
「克隆仓库、填一个 LLM key、直接 `pnpm dev`」这条路径能走通；强制要求 token
会让第一次跑起来就卡在 401，而此时用户根本不知道要配什么。
"""

from __future__ import annotations

from hmac import compare_digest
from typing import Annotated

import structlog
from fastapi import Header, HTTPException, status

from agent_service.config import get_settings

log = structlog.get_logger(__name__)


async def require_internal_token(
    x_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    """校验 `X-Internal-Token`。作为 FastAPI 依赖使用。"""
    expected = get_settings().internal_api_token
    if expected is None:
        return

    # 定长比较：普通 `!=` 会在第一个不同字节就返回，逐字节爆破可行。
    # 本机场景下风险不高，但正确写法只多一个函数调用，没有不写的理由
    if x_internal_token is None or not compare_digest(
        x_internal_token, expected.get_secret_value()
    ):
        log.warning("auth.rejected", reason="X-Internal-Token 不匹配或缺失")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_INTERNAL_TOKEN",
                    "message": "X-Internal-Token 缺失或不匹配",
                }
            },
        )
