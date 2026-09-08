"""web_fetch：WebFetcher → ToolResult。提取失败用 DataQuality 声明，不让模型编。"""

from __future__ import annotations

from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import DataQuality, ToolResult
from agent_service.tools._result import fail_invalid, fail_provider, fail_unavailable
from agent_service.tools.deps import ToolDeps
from agent_service.tools.web.models import WebPageData
from agent_service.tools.web.untrusted import annotate_untrusted


async def run_web_fetch(deps: ToolDeps, *, url: str) -> ToolResult[WebPageData]:
    target = url.strip()
    if not target:
        return fail_invalid("web_fetch", "URL 为空")
    if deps.fetcher is None:
        return fail_unavailable(
            tool="web_fetch",
            provider="web_fetch",
            message="抓取器未初始化",
        )
    try:
        page = await deps.fetcher.fetch(target)
    except ProviderError as exc:
        return fail_provider("web_fetch", exc)

    quality = None
    if page.text is None:
        quality = DataQuality(
            completeness="partial",
            missing_fields=["text"],
            caveats=["无法从页面提取正文"],
        )
    result = ToolResult.success(
        WebPageData(
            url=page.url,
            final_url=page.final_url,
            title=page.title,
            text=page.text,
            status_code=page.status_code,
            content_type=page.content_type,
        ),
        provenance=page.provenance,
        quality=quality,
    )
    return annotate_untrusted(deps, result, source=page.final_url)
