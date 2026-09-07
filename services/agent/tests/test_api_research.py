"""研究流 SSE 端点（P1-11 验收）。

分帧规则单独测：帧格式错了的表现是前端**静默**收不到事件——没有异常、
没有日志，只有一个不动的进度条。这类 bug 在端到端层面极难定位，
所以在这一层就用一个独立的解析器把编码结果反解回来校验。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import pytest
from agents.testing import ScriptedModel, assistant_message
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agent_service.agents.placeholder import NOT_IMPLEMENTED_GAP, PlaceholderRunner
from agent_service.agents.research_manager import PlannerAgent, build_research_manager
from agent_service.api import research as research_api
from agent_service.api.sse import encode_comment, encode_event
from agent_service.config import get_settings
from agent_service.main import create_app
from agent_service.models.registry import ModelRegistry
from agent_service.observability.event_bus import EventBus
from agent_service.orchestrator.executor import TaskContext
from agent_service.orchestrator.state import ResearchState
from agent_service.schemas.common import AgentName
from agent_service.schemas.events import EventType, WarningEvent, WarningPayload
from agent_service.schemas.plan import ResearchTask
from agent_service.testing import (
    IsolatedExecutionLimits,
    IsolatedProviderCredentials,
    IsolatedSettings,
)

_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "MOONSHOT_API_KEY",
    "DEEPSEEK_API_KEY",
    "ZHIPU_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "INTERNAL_API_TOKEN",
)


def _plan_json(**overrides: Any) -> str:
    body: dict[str, Any] = {
        "question_type": "crypto",
        "interpretation": "用户想了解 Hyperliquid 的协议收入",
        "entities": [],
        "tasks": [
            {
                "id": "t1",
                "agent": "crypto_research",
                "objective": "获取 Hyperliquid 过去 90 天的手续费收入",
                "entities": [],
                "suggested_tools": [],
                "depends_on": [],
                "priority": 0,
            }
        ],
        "report_sections": ["Overview"],
        "assumptions": [],
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


def _scripted_planner(*replies: str) -> PlannerAgent:
    registry = ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
        )
    )
    built = build_research_manager(registry, IsolatedExecutionLimits())
    scripted = ScriptedModel([[assistant_message(reply)] for reply in replies])
    return PlannerAgent(
        agent=built.agent.clone(model=scripted),
        strategy=built.strategy,
        entry=built.entry,
        prompt=built.prompt,
    )


@pytest.fixture
def only_deepseek(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """把配置钉死成「只充了 DeepSeek」。

    必须逐个清掉 key 环境变量：`get_settings()` 会读仓库根的 `.env.local`，
    不隔离的话测试结果取决于开发者本地充了哪几家的 key。
    """
    for name in _KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    get_settings.cache_clear()

    yield

    get_settings.cache_clear()


@pytest.fixture
def client(only_deepseek: None, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """带 ScriptedModel 的端点客户端。

    换掉 `build_research_manager` 而不是往 registry 里塞替身：端点对模型的
    唯一依赖就是这个工厂函数，从这一个点注入最省事。
    """
    del only_deepseek
    monkeypatch.setattr(
        research_api,
        "build_research_manager",
        lambda registry, limits, model_id=None: _scripted_planner(_plan_json()),
    )
    return TestClient(create_app())


@pytest.fixture
def unpatched_client(only_deepseek: None) -> TestClient:
    """走真实 `build_research_manager` 的客户端，用于验证模型解析失败的处理。

    这些用例不会真的调用 LLM——模型解析在返回响应之前就失败了，
    这正是要验证的行为。
    """
    del only_deepseek
    return TestClient(create_app())


# ─────────────────────────────────────────────────────────────────────────────
# 分帧
# ─────────────────────────────────────────────────────────────────────────────


def parse_frames(raw: str) -> list[dict[str, Any]]:
    """按 SSE 规范反解帧，只保留带 data 的。

    刻意不复用生产代码的任何常量：这里要验的就是「产出的字节符合规范」，
    用同一份常量对着编码/解码只能证明它自洽。
    """
    events: list[dict[str, Any]] = []
    for block in raw.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        data = "\n".join(
            line.removeprefix("data:").lstrip() for line in lines if line.startswith("data:")
        )
        if data:
            events.append(json.loads(data))
    return events


def test_frame_has_id_event_and_data_lines() -> None:
    bus = EventBus("sess-1")
    event = bus.emit(
        WarningEvent,
        payload=WarningPayload(code="quota_low", message="注意"),
        message="注意",
    )

    frame = encode_event(event).decode()

    assert frame.startswith("id: 1\nevent: research\ndata: {")
    # 空行结尾是分帧符，少一个换行会让客户端一直等下一帧
    assert frame.endswith("}\n\n")


def test_data_stays_on_one_line_even_with_multiline_text() -> None:
    """message 里的换行必须被 JSON 转义。

    裸换行会被客户端当成新字段，后半段文本直接消失——而 LLM 产出的文案里
    出现换行是常态。
    """
    bus = EventBus("sess-1")
    event = bus.emit(
        WarningEvent,
        payload=WarningPayload(code="data_gap", message="第一行\n第二行"),
        message="标题\n正文",
    )

    body = encode_event(event).decode().split("data: ", 1)[1]

    assert body.count("\n") == 2  # 只有结尾的分帧空行
    assert parse_frames(encode_event(event).decode())[0]["message"] == "标题\n正文"


def test_comment_frame_carries_no_data() -> None:
    assert parse_frames(encode_comment("stream open").decode()) == []


# ─────────────────────────────────────────────────────────────────────────────
# 鉴权（§11.3）
# ─────────────────────────────────────────────────────────────────────────────


def test_token_is_optional_when_unset(client: TestClient) -> None:
    """没配 token 时放行——否则「填一个 LLM key 就能跑起来」这条路径会卡在 401。"""
    response = client.post("/v1/research/stream", json={"question": "Hyperliquid 怎么样？"})
    assert response.status_code == 200


def test_rejects_missing_token_when_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "shared-secret")
    get_settings.cache_clear()

    response = client.post("/v1/research/stream", json={"question": "Hyperliquid 怎么样？"})

    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "INVALID_INTERNAL_TOKEN"


def test_accepts_matching_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "shared-secret")
    get_settings.cache_clear()

    response = client.post(
        "/v1/research/stream",
        json={"question": "Hyperliquid 怎么样？"},
        headers={"X-Internal-Token": "shared-secret"},
    )

    assert response.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 请求校验与模型解析
# ─────────────────────────────────────────────────────────────────────────────


def test_rejects_empty_question(client: TestClient) -> None:
    assert client.post("/v1/research/stream", json={"question": ""}).status_code == 422


def test_rejects_oversized_question(client: TestClient) -> None:
    """超长问题会把 §9.8 的缓存前缀冲掉，且规划成本随之膨胀。"""
    response = client.post("/v1/research/stream", json={"question": "x" * 3_000})
    assert response.status_code == 422


def test_unknown_model_fails_before_the_stream_opens(unpatched_client: TestClient) -> None:
    """模型 id 打错属于请求错误，要用 HTTP 状态码表达。

    塞进事件流里的话，前端会在「已开始研究」的 UI 状态下收到失败事件，
    白白多渲染一次任务树再撤掉。
    """
    response = unpatched_client.post(
        "/v1/research/stream",
        json={"question": "Hyperliquid 怎么样？", "model_id": "deepseek:nope"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "UNKNOWN_MODEL"


def test_unconfigured_provider_reports_503(unpatched_client: TestClient) -> None:
    """没配 key 的模型要在解析阶段就报 503，而不是等 LLM 调用时拿 401。"""
    response = unpatched_client.post(
        "/v1/research/stream",
        json={"question": "Hyperliquid 怎么样？", "model_id": "openai:gpt-5.6-terra"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "MODEL_NOT_AVAILABLE"


# ─────────────────────────────────────────────────────────────────────────────
# 流内容
# ─────────────────────────────────────────────────────────────────────────────


def test_stream_declares_event_stream_and_disables_buffering(client: TestClient) -> None:
    with client.stream(
        "POST", "/v1/research/stream", json={"question": "Hyperliquid 怎么样？"}
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        # 少了这个头，反代会把事件攒到缓冲区满才下发，"实时"就没了
        assert response.headers["x-accel-buffering"] == "no"


def test_stream_delivers_the_whole_session(client: TestClient) -> None:
    response = client.post("/v1/research/stream", json={"question": "Hyperliquid 怎么样？"})
    events = parse_frames(response.text)

    types = [event["type"] for event in events]
    assert types[0] == EventType.SESSION_STARTED
    assert types[-1] == EventType.SESSION_COMPLETED
    assert EventType.PLAN_CREATED in types
    assert EventType.AGENT_COMPLETED in types


def test_seq_is_gapless_and_matches_the_sse_id(client: TestClient) -> None:
    """`id:` 与 payload 里的 seq 必须一致：断线重连要靠 `Last-Event-ID` 定位。"""
    raw = client.post("/v1/research/stream", json={"question": "Hyperliquid 怎么样？"}).text

    ids = [
        int(line.removeprefix("id:").strip()) for line in raw.split("\n") if line.startswith("id:")
    ]
    seqs = [event["seq"] for event in parse_frames(raw)]

    assert ids == seqs
    assert seqs == list(range(1, len(seqs) + 1))


def test_session_id_from_the_caller_is_used(client: TestClient) -> None:
    """DB 的唯一 writer 是 Next.js（§11.1），session 行先建、id 由它下传。"""
    raw = client.post(
        "/v1/research/stream",
        json={"question": "Hyperliquid 怎么样？", "session_id": "sess-from-next"},
    ).text

    assert {event["session_id"] for event in parse_frames(raw)} == {"sess-from-next"}


# ─────────────────────────────────────────────────────────────────────────────
# 占位 runner
# ─────────────────────────────────────────────────────────────────────────────


def test_placeholder_still_drives_the_agent_lifecycle(client: TestClient) -> None:
    """占位 runner 也要产生完整的 started/progress/completed。

    前端 Activity Panel（P1-12）要靠这三个事件验证渲染，缺一个就没法开工。
    """
    events = parse_frames(
        client.post("/v1/research/stream", json={"question": "Hyperliquid 怎么样？"}).text
    )
    types = [event["type"] for event in events]

    assert types.index(EventType.AGENT_STARTED) < types.index(EventType.AGENT_PROGRESS)
    assert types.index(EventType.AGENT_PROGRESS) < types.index(EventType.AGENT_COMPLETED)


async def test_placeholder_discloses_that_no_data_was_fetched() -> None:
    """占位实现刻意不假装成功。

    返回一段编造的 summary 的话，Phase 2 接手前没人会发现研究流程其实是空的。
    这条缺口会一路走到报告的「数据限制」章节。
    """
    bus = EventBus("sess-1", heartbeat_interval_s=60.0)
    state = ResearchState("sess-1", "问题", bus=bus)
    task = ResearchTask(id="t1", agent=AgentName.CRYPTO_RESEARCH, objective="获取手续费收入")

    finding = await PlaceholderRunner("deepseek:deepseek-v4-pro").run(
        TaskContext(task=task, upstream=(), missing_upstream=()), state
    )

    assert finding.data_gaps == [NOT_IMPLEMENTED_GAP]


# ─────────────────────────────────────────────────────────────────────────────
# 生命周期
# ─────────────────────────────────────────────────────────────────────────────


async def test_consumer_leaving_cancels_the_pipeline() -> None:
    """客户端断连后必须取消 pipeline。

    不取消的话，没人消费事件、更没人落库，它却会继续调用 LLM——
    纯粹是在烧 token。
    """
    bus = EventBus("sess-1", heartbeat_interval_s=0.01)
    started = asyncio.Event()

    async def never_ends() -> None:
        started.set()
        await asyncio.sleep(3600)

    pipeline = asyncio.create_task(never_ends())
    stream = research_api._pump(bus, pipeline, session_id="sess-1")  # pyright: ignore[reportPrivateUsage]

    assert await anext(stream) == encode_comment("stream open")
    await started.wait()
    await stream.aclose()

    await asyncio.sleep(0)
    assert pipeline.cancelled() or pipeline.cancelling()


async def test_stream_ends_even_if_the_pipeline_crashes() -> None:
    """pipeline 抛出未捕获异常时，流也必须结束。

    少了这层安全网，消费端会永远收心跳，既不知道已经失败、也不会关连接——
    表现为一个永远转圈的进度条。
    """
    bus = EventBus("sess-1", heartbeat_interval_s=0.01)

    async def explodes() -> None:
        raise RuntimeError("编排层自己漏了一个异常")

    pipeline = asyncio.create_task(explodes())
    frames = [
        frame
        async for frame in research_api._pump(bus, pipeline, session_id="sess-1")  # pyright: ignore[reportPrivateUsage]
    ]

    assert frames == [encode_comment("stream open")]
    assert bus.closed
