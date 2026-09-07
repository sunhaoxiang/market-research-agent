"""P1-1 数据契约测试：序列化/反序列化与判别联合。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import get_args

import pytest
from pydantic import ValidationError

from agent_service.schemas import (
    TERMINAL_EVENT_TYPES,
    AgentName,
    AssetType,
    Claim,
    ClaimDraft,
    ConfidenceLevel,
    DataProvenance,
    Entity,
    EpistemicType,
    EventType,
    QuestionType,
    ResearchEvent,
    ResearchEventAdapter,
    ResearchPlan,
    ResearchTask,
    Source,
    SourceReliability,
    SourceType,
    ToolError,
    ToolErrorCode,
    ToolResult,
)
from agent_service.schemas.events import (
    HeartbeatEvent,
    SessionStartedEvent,
    ToolStartedEvent,
    ToolStartedPayload,
)
from agent_service.utils.ids import new_id, uuid7

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


# ─── UUIDv7 ──────────────────────────────────────────────────────────────────


def test_uuid7_version_and_variant() -> None:
    value = uuid7()
    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


def test_uuid7_is_strictly_monotonic() -> None:
    """字典序即时间序是选用 v7 的全部理由（§10.1），必须守住。

    2000 个 id 会在同一毫秒内挤出多个，因此这同时验证了 rand_a 计数器
    （RFC 9562 §6.2 Method 1）确实在工作——纯随机 rand_a 会让这个断言失败。
    """
    ids = [new_id() for _ in range(2000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_uuid7_is_monotonic_under_concurrency() -> None:
    """多线程下计数器不能错乱：编排层是 asyncio + 线程池混合的。"""
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: new_id(), range(2000)))

    assert len(set(ids)) == len(ids), "并发生成出现了重复 id"


# ─── 事件判别联合 ────────────────────────────────────────────────────────────


def test_event_roundtrip_preserves_payload_type() -> None:
    event = ToolStartedEvent(
        seq=3,
        session_id="s-1",
        ts=NOW,
        message="正在查询 DefiLlama 的 HYPE TVL",
        payload=ToolStartedPayload(
            call_id="c-1",
            tool="get_protocol_tvl",
            agent=AgentName.CRYPTO_RESEARCH,
            task_id="t1",
            input_summary="protocol=hyperliquid",
        ),
    )

    restored = ResearchEventAdapter.validate_json(event.model_dump_json())

    assert isinstance(restored, ToolStartedEvent)
    assert restored.type is EventType.TOOL_STARTED
    assert restored.payload.tool == "get_protocol_tvl"
    assert restored.payload.agent is AgentName.CRYPTO_RESEARCH
    assert restored.ts == NOW


def test_event_discriminator_picks_correct_variant() -> None:
    raw = {
        "seq": 1,
        "session_id": "s-1",
        "ts": NOW.isoformat(),
        "type": "session_started",
        "payload": {"question": "HYPE 值得关注吗", "model_id": "deepseek:deepseek-v4-pro"},
    }
    event = ResearchEventAdapter.validate_python(raw)

    assert isinstance(event, SessionStartedEvent)
    assert event.payload.question == "HYPE 值得关注吗"


def test_event_with_wrong_payload_shape_is_rejected() -> None:
    """判别联合必须真的校验 payload，而不是宽松接受任意 dict。"""
    with pytest.raises(ValidationError):
        ResearchEventAdapter.validate_python(
            {
                "seq": 1,
                "session_id": "s-1",
                "ts": NOW.isoformat(),
                "type": "tool_started",
                "payload": {"unrelated": "field"},  # 缺 call_id / tool / agent
            }
        )


def test_unknown_event_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResearchEventAdapter.validate_python(
            {"seq": 1, "session_id": "s-1", "ts": NOW.isoformat(), "type": "nope", "payload": None}
        )


def test_payloadless_event_serializes_null_payload() -> None:
    event = HeartbeatEvent(seq=7, session_id="s-1", ts=NOW)
    dumped = event.model_dump(mode="json")

    assert dumped["type"] == "heartbeat"
    assert dumped["payload"] is None
    # 字段必须始终出现在线上格式里——TS 侧的 required 标记依赖这个前提（见 export.py）
    assert set(dumped) == {"seq", "session_id", "ts", "message", "type", "payload"}


def test_every_event_type_has_a_variant() -> None:
    """新增 EventType 时若忘了定义对应的事件类，这里会失败。

    这是 §12.2 事件清单与代码之间唯一的自动化一致性检查。
    """
    union_type, _ = get_args(ResearchEvent)
    covered = {variant.model_fields["type"].default for variant in get_args(union_type)}
    assert covered == set(EventType)


def test_terminal_event_types_match_frontend_constant() -> None:
    expected = {
        EventType.SESSION_COMPLETED,
        EventType.SESSION_FAILED,
        EventType.SESSION_CANCELLED,
    }
    assert set(TERMINAL_EVENT_TYPES) == expected


# ─── 领域模型 ────────────────────────────────────────────────────────────────


def test_research_plan_roundtrip() -> None:
    plan = ResearchPlan(
        question_type=QuestionType.CRYPTO,
        interpretation="评估 HYPE 的基本面与近期催化剂",
        entities=[Entity(type=AssetType.CRYPTO, symbol="HYPE", chain="hyperliquid")],
        tasks=[
            ResearchTask(
                id="t1",
                agent=AgentName.CRYPTO_RESEARCH,
                objective="获取 HYPE 的市值、TVL 与近 30 日价格走势",
                entities=[Entity(type=AssetType.CRYPTO, symbol="HYPE")],
                suggested_tools=["get_crypto_market_data", "get_protocol_tvl"],
            ),
            ResearchTask(
                id="t2",
                agent=AgentName.WEB_RESEARCH,
                objective="检索近期与 HYPE 相关的重要新闻",
                depends_on=["t1"],
            ),
        ],
        report_sections=["overview", "catalysts", "risks"],
    )

    restored = ResearchPlan.model_validate_json(plan.model_dump_json())

    assert restored == plan
    assert restored.tasks[1].depends_on == ["t1"]


def test_agent_name_must_be_a_known_enum_member() -> None:
    """plan 里的 agent 由 schema 兜底校验，编排层无需再防御非法值。"""
    with pytest.raises(ValidationError):
        ResearchTask.model_validate({"id": "t1", "agent": "hedge_fund_manager", "objective": "x"})


def test_claim_draft_defaults_to_no_sources() -> None:
    draft = ClaimDraft(
        text="HYPE 的 TVL 在过去 30 天增长了 18%",
        epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
        confidence=ConfidenceLevel.HIGH,
    )
    assert draft.source_refs == []


def test_claim_requires_source_flag_tracks_epistemic_type() -> None:
    """§15.3 第三重强制（代码层降级）依赖这个判定。"""
    backed = Claim(
        text="NVDA 上季度营收 570 亿美元",
        epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
        confidence=ConfidenceLevel.HIGH,
    )
    predicted = Claim(
        text="NVDA 下季度营收可能继续增长",
        epistemic_type=EpistemicType.PREDICTION,
        confidence=ConfidenceLevel.LOW,
    )

    assert backed.requires_source is True
    assert predicted.requires_source is False


def test_source_defaults_to_unknown_reliability() -> None:
    """默认最低可信度：来源分级必须由抓取方显式提升，不能靠默认值蒙混（§15.2）。"""
    source = Source(
        ref="s1",
        url="https://example.com/a",
        url_canonical="https://example.com/a",
        source_type=SourceType.WEB,
        retrieved_at=NOW,
    )
    assert source.reliability is SourceReliability.UNKNOWN
    assert source.citation_index is None


# ─── ToolResult ──────────────────────────────────────────────────────────────


def test_tool_result_success_carries_provenance() -> None:
    result = ToolResult[Entity].success(
        data=Entity(type=AssetType.STOCK, symbol="NVDA"),
        provenance=DataProvenance(provider="fmp", endpoint="/profile", retrieved_at=NOW),
    )

    assert result.ok is True
    assert result.data is not None
    assert result.data.symbol == "NVDA"
    assert result.error is None
    assert result.provenance is not None
    assert result.provenance.provider == "fmp"


def test_tool_result_failure_has_no_data() -> None:
    """错误是结构化返回值而非异常（§8.1），Agent 据此决定换工具还是声明 data gap。"""
    result = ToolResult[Entity].failure(
        ToolError(code=ToolErrorCode.QUOTA_EXHAUSTED, message="FMP 当日免费额度已用尽")
    )

    assert result.ok is False
    assert result.data is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.QUOTA_EXHAUSTED


def test_tool_result_validates_data_type() -> None:
    with pytest.raises(ValidationError):
        ToolResult[Entity].model_validate({"ok": True, "data": {"type": "bond", "symbol": "X"}})
