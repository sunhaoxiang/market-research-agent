"""研究流 SSE 端点（§11.2 / §16.2，P1-11）。

这一层只做三件事：把请求变成一个 pipeline 任务、把事件总线的输出编码成 SSE、
保证两者的生命周期绑在一起。业务逻辑全在 `orchestrator/`。

**流一定会终止，这是本模块最重要的不变量。** 三条保障：
  1. 正常路径由终态事件结束 `bus.stream()`（§12）；
  2. pipeline 任务无论正常结束还是异常退出，`done_callback` 都会 `close()` 总线——
     否则一次未预期的异常会让消费端永远收心跳而不知道已经没救了；
  3. 消费端提前离开（客户端断连）时取消 pipeline 任务。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent_service.agents.fact_checker import build_fact_checker
from agent_service.agents.report_writer import build_report_writer
from agent_service.agents.research_manager import build_research_manager
from agent_service.agents.runner import SubAgentRunner
from agent_service.api.auth import require_internal_token
from agent_service.api.sse import SSE_HEADERS, encode_comment, encode_event
from agent_service.config import apply_limit_overrides, get_settings
from agent_service.models.catalog import UnknownModelError
from agent_service.models.registry import ProviderUnavailableError
from agent_service.observability.event_bus import EventBus
from agent_service.orchestrator.intent import build_intent_classifier
from agent_service.orchestrator.pipeline import run_research
from agent_service.schemas.common import ModelRole
from agent_service.utils.ids import new_id

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from agent_service.models.registry import ModelRegistry

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/research", tags=["research"])


class ResearchRuns:
    """进行中的 pipeline，按 session_id 索引，给 cancel 用（P6-7）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[object]] = {}

    def register(self, session_id: str, task: asyncio.Task[object]) -> None:
        existing = self._tasks.get(session_id)
        if existing is not None and not existing.done():
            existing.cancel()
        self._tasks[session_id] = task

        def _forget(done: asyncio.Task[object]) -> None:
            current = self._tasks.get(session_id)
            if current is done:
                self._tasks.pop(session_id, None)

        task.add_done_callback(_forget)

    def cancel(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        if task is None or task.done():
            return False
        return task.cancel()


MAX_QUESTION_CHARS = 2_000
"""问题长度上限。不设限的话，一段几十 KB 的粘贴内容会直接进 planner 的 prompt，
既贵又会把 §9.8 的缓存前缀冲掉。"""


class RoleModelOverrides(BaseModel):
    planner: str | None = None
    balanced: str | None = None
    fast: str | None = None
    writing: str | None = None


class LimitsOverride(BaseModel):
    max_tasks_per_plan: int | None = Field(default=None, ge=1, le=10)
    max_parallel_tasks: int | None = Field(default=None, ge=1, le=6)
    max_tool_calls_per_agent: int | None = Field(default=None, ge=1, le=20)
    max_supplement_rounds: int | None = Field(default=None, ge=0, le=2)
    task_timeout_s: float | None = Field(default=None, gt=0)
    total_timeout_s: float | None = Field(default=None, gt=0)
    max_session_cost_usd: float | None = Field(default=None, gt=0)


class ResearchOptions(BaseModel):
    """单次研究的用户设置（P6-8）。不写进进程级 Settings。"""

    role_models: RoleModelOverrides | None = None
    limits: LimitsOverride | None = None
    report_language: Literal["zh", "en"] | None = None


class ResearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    session_id: str | None = Field(
        default=None,
        description=(
            "由 Next.js 侧在建 session 行时生成并传入（§11.1：DB 的唯一 writer 是 Next）。"
            "缺省时 Python 自行生成，仅便于 curl 直接调试"
        ),
    )
    model_id: str | None = Field(default=None, description="覆盖本次会话全部角色。缺省走角色映射")
    options: ResearchOptions | None = None


@router.post(
    "/stream",
    dependencies=[Depends(require_internal_token)],
    response_class=StreamingResponse,
)
async def stream_research(request: ResearchRequest, http_request: Request) -> StreamingResponse:
    """启动一次研究并以 SSE 推送事件。

    模型解析放在返回响应**之前**：模型 id 打错或没配 key 属于请求错误，
    应当以 400/503 + §16.3 的错误结构返回。塞进事件流里只会让前端在
    「已经开始研究」的 UI 状态下收到一个失败事件，白白多一次渲染。
    """
    registry: ModelRegistry = http_request.app.state.registry
    options = request.options or ResearchOptions()
    registry = _registry_for_request(registry, request.model_id, options.role_models)
    limits = apply_limit_overrides(
        get_settings().limits,
        None if options.limits is None else options.limits.model_dump(exclude_none=True),
    )
    language = options.report_language or "zh"
    session_id = request.session_id or new_id()

    try:
        planner = build_research_manager(registry, limits, model_id=request.model_id)
        writer = build_report_writer(registry, language=language)
        fact_checker = build_fact_checker(registry)
    except UnknownModelError as error:
        raise _http_error(status.HTTP_400_BAD_REQUEST, "UNKNOWN_MODEL", str(error)) from error
    except ProviderUnavailableError as error:
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "MODEL_NOT_AVAILABLE", str(error)
        ) from error

    try:
        classifier = build_intent_classifier(registry)
    except (UnknownModelError, ProviderUnavailableError):
        classifier = None

    bus = EventBus(session_id)
    runtime = getattr(http_request.app.state, "provider_runtime", None)
    clock = None if runtime is None else runtime.clock
    search = getattr(http_request.app.state, "search_provider", None)
    fetcher = getattr(http_request.app.state, "web_fetcher", None)
    runner = SubAgentRunner(
        registry,
        limits=limits,
        search=search,
        fetcher=fetcher,
        coingecko=getattr(http_request.app.state, "coingecko", None),
        defillama=getattr(http_request.app.state, "defillama", None),
        hyperliquid=getattr(http_request.app.state, "hyperliquid", None),
        sec_edgar=getattr(http_request.app.state, "sec_edgar", None),
        fmp=getattr(http_request.app.state, "fmp", None),
        clock=clock,
        fallback_model_id=planner.model_id,
    )

    log.info(
        "research.stream_started",
        session_id=session_id,
        model_id=planner.model_id,
        question_chars=len(request.question),
    )

    pipeline = asyncio.create_task(
        run_research(
            request.question,
            planner=planner,
            runner=runner,
            writer=writer,
            fact_checker=fact_checker,
            limits=limits,
            bus=bus,
            session_id=session_id,
            classifier=classifier,
            search=search,
            fetcher=fetcher,
            clock=clock,
        )
    )
    runs: ResearchRuns = http_request.app.state.research_runs
    runs.register(session_id, pipeline)

    return StreamingResponse(
        _pump(bus, pipeline, session_id=session_id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/{session_id}/cancel", dependencies=[Depends(require_internal_token)])
async def cancel_research(session_id: str, http_request: Request) -> dict[str, bool]:
    """取消一次进行中的研究。pipeline 会发 `SESSION_CANCELLED`，原 SSE 消费者负责落库。"""
    runs: ResearchRuns = http_request.app.state.research_runs
    if not runs.cancel(session_id):
        raise _http_error(status.HTTP_404_NOT_FOUND, "NOT_RUNNING", "没有进行中的研究")
    log.info("research.cancelled", session_id=session_id)
    return {"cancelled": True}


async def _pump(
    bus: EventBus,
    # `object` 而非 `ResearchOutcome`：这一层只关心任务的生命周期
    # （done / cancel / exception），从不读它的返回值
    pipeline: asyncio.Task[object],
    *,
    session_id: str,
) -> AsyncGenerator[bytes]:
    """把总线里的事件编码成 SSE 帧。

    先吐一个注释帧再进循环：有些代理要等到收到第一个字节才认为响应已开始，
    而规划阶段的第一个事件可能要等几十秒（planner 实测 30-45s）。
    """
    pipeline.add_done_callback(_finalize(bus, session_id))

    try:
        # 必须在 try 内部：客户端可能在收到这一帧之后、第一个业务事件之前就断开，
        # 而 `aclose()` 是在当前挂起点抛 GeneratorExit——yield 写在 try 外面的话
        # 这条断连路径不会触发下面的 finally，pipeline 就没人取消了
        yield encode_comment("stream open")

        async for event in bus.stream():
            yield encode_event(event)
    finally:
        # 消费端也可能是提前离开的：客户端断连会让 FastAPI 关掉这个生成器。
        # 这时必须取消 pipeline——没人会消费它的事件、更没人会落库，
        # 让它继续跑下去纯粹是在烧 token
        if not pipeline.done():
            log.warning("research.consumer_gone", session_id=session_id)
            _ = pipeline.cancel()


def _finalize(bus: EventBus, session_id: str) -> Callable[[asyncio.Task[object]], None]:
    """pipeline 结束后的收尾。

    做成 done callback 而不是在 `_pump` 的 `finally` 里 `await`：客户端断连时
    `finally` 是在生成器被关闭的过程中执行的，此处再 await 一个刚被我们取消的
    任务，很容易变成「等一个永远等不到的结果」。回调由事件循环调用，没这个问题。

    两件事都是安全网，正常路径下都不生效：
      - `close()` 保证消费端不会因为 pipeline 意外崩溃而永远收心跳
        （正常路径下终态事件已经让 `stream()` 返回了，此处是幂等的）；
      - 取回异常，避免事件循环退出时的 "Task exception was never retrieved"。
    """

    def callback(task: asyncio.Task[object]) -> None:
        bus.close()

        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            # 走到这里说明 pipeline 的兜底 except 自己也漏了，属于代码 bug。
            # 客户端只会看到流突然结束，所以日志是唯一线索
            log.error("research.pipeline_crashed", session_id=session_id, exc_info=error)

    return callback


def _registry_for_request(
    registry: ModelRegistry,
    model_id: str | None,
    role_models: RoleModelOverrides | None,
) -> ModelRegistry:
    """§9.5：请求里选的模型覆盖全部角色；否则只叠按角色指定的那些。"""
    overrides: dict[str, str] = {}
    if model_id:
        overrides = {role.value: model_id for role in ModelRole}
    elif role_models is not None:
        overrides = {
            role: value
            for role, value in role_models.model_dump(exclude_none=True).items()
            if isinstance(value, str) and value
        }
    return registry.with_role_overrides(overrides) if overrides else registry


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    """按 §16.3 的统一错误结构构造异常。"""
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message}},
    )
