"""Web Research Agent（P2-6）：产出带来源的 ResearchFinding。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from agents.testing import ScriptedModel, assistant_message, function_call
from pydantic import SecretStr

from agent_service.agents.placeholder import NOT_IMPLEMENTED_GAP
from agent_service.agents.runner import SubAgentRunner
from agent_service.agents.runtime import run_tool_agent
from agent_service.agents.web_research import (
    assemble_finding,
    build_web_research,
    web_research_user_message,
)
from agent_service.models.registry import ModelRegistry
from agent_service.observability.event_bus import EventBus
from agent_service.observability.sdk_events import AgentRunTranslator
from agent_service.orchestrator.executor import TaskContext
from agent_service.orchestrator.state import ResearchState
from agent_service.providers.search import SearchHit, SearchPage, SearchQuery
from agent_service.schemas.claims import ClaimDraft
from agent_service.schemas.common import AgentName, ConfidenceLevel, EpistemicType
from agent_service.schemas.events import EventType
from agent_service.schemas.findings import AgentFinding
from agent_service.schemas.plan import ResearchTask
from agent_service.schemas.tools import DataProvenance, ToolResult
from agent_service.testing import (
    IsolatedExecutionLimits,
    IsolatedProviderCredentials,
    IsolatedSettings,
)
from agent_service.tools.deps import ToolDeps
from agent_service.tools.web.collector import SourceCollector, stamp_refs
from agent_service.tools.web.models import WebSearchData, WebSearchHit

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_URL = "https://www.theblock.co/hyperliquid-fee-share"


def _registry() -> ModelRegistry:
    return ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
        )
    )


def _task() -> ResearchTask:
    return ResearchTask(
        id="t1",
        agent=AgentName.WEB_RESEARCH,
        objective="查找 HYPE 最近的重要新闻",
        suggested_tools=["news_search"],
    )


def _hit() -> SearchHit:
    return SearchHit(
        url=_URL,
        title="Hyperliquid discusses fee sharing",
        snippet="The protocol may share trading fees with HYPE holders.",
        raw_content=None,
        score=0.9,
        published_at=_NOW,
        domain="theblock.co",
    )


class FakeSearch:
    name = "fake"

    def __init__(self, page: SearchPage) -> None:
        self.page = page
        self.queries: list[SearchQuery] = []

    async def search(self, query: SearchQuery) -> SearchPage:
        self.queries.append(query)
        return self.page

    async def aclose(self) -> None:
        return None


def _finding_json() -> str:
    return json.dumps(
        {
            "summary": "近期有报道称 Hyperliquid 在讨论把部分交易手续费分享给 HYPE 持有人。",
            "claims": [
                {
                    "text": "Hyperliquid 正在讨论将部分交易手续费分享给 HYPE 持有人。",
                    "epistemic_type": "source_backed_fact",
                    "confidence": "medium",
                    "source_refs": ["s1"],
                }
            ],
            "metrics": [],
            "data_gaps": [],
        },
        ensure_ascii=False,
    )


def test_assemble_finding_maps_short_refs() -> None:
    collector = SourceCollector()
    result = ToolResult.success(
        WebSearchData(
            query="HYPE",
            hits=[WebSearchHit(url=_URL, title="Fee share", snippet="holders")],
            topic="news",
        ),
        provenance=DataProvenance(provider="tavily", endpoint="/search", retrieved_at=_NOW),
    )
    stamp_refs(collector, result)
    draft = AgentFinding(
        summary="有手续费分享讨论。",
        claims=[
            ClaimDraft(
                text="Hyperliquid 在讨论手续费分享。",
                epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
                confidence=ConfidenceLevel.MEDIUM,
                source_refs=["s1"],
            )
        ],
    )
    finding = assemble_finding(_task(), draft, collector)
    assert len(finding.sources) == 1
    assert finding.sources[0].ref == "s1"
    assert finding.sources[0].url == _URL
    assert finding.claims[0].source_ids == [finding.sources[0].id]
    assert finding.claims[0].epistemic_type is EpistemicType.SOURCE_BACKED_FACT


def test_missing_source_ref_downgrades_to_fact() -> None:
    draft = AgentFinding(
        summary="没有搜到。",
        claims=[
            ClaimDraft(
                text="HYPE 明天会涨。",
                epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
                confidence=ConfidenceLevel.HIGH,
                source_refs=["s9"],
            )
        ],
    )
    finding = assemble_finding(_task(), draft, SourceCollector())
    assert finding.claims[0].epistemic_type is EpistemicType.FACT
    assert finding.claims[0].source_ids == []
    assert any("s9" in gap for gap in finding.data_gaps)
    assert any("未获得任何网页来源" in gap for gap in finding.data_gaps)


def test_duplicate_url_reuses_ref() -> None:
    collector = SourceCollector()
    hit = WebSearchHit(url=_URL, title="A", snippet="a")
    page = ToolResult.success(
        WebSearchData(query="q", hits=[hit, hit.model_copy()], topic="news"),
        provenance=DataProvenance(provider="tavily", endpoint="/search", retrieved_at=_NOW),
    )
    stamped = stamp_refs(collector, page)
    assert stamped.data is not None
    assert stamped.data.hits[0].ref == "s1"
    assert stamped.data.hits[1].ref == "s1"
    assert len(collector.sources()) == 1


def test_web_research_prompt_hash_is_stable() -> None:
    first = build_web_research(_registry())
    second = build_web_research(_registry())
    assert first.prompt.hash == second.prompt.hash
    assert first.prompt.hash != ""


def test_user_message_keeps_date_out_of_system_prompt() -> None:
    text = web_research_user_message(_task(), now=_NOW)
    assert "2026-09-08" in text
    assert "查找 HYPE 最近的重要新闻" in text
    built = build_web_research(_registry())
    assert "2026-09-08" not in str(built.agent.instructions)


async def test_scripted_web_agent_produces_finding_with_sources() -> None:
    """验收：对「HYPE 最近有什么新闻」产出带来源的 finding。"""
    search = FakeSearch(
        SearchPage(
            query="HYPE news",
            hits=(_hit(),),
            provenance=DataProvenance(provider="tavily", endpoint="/search", retrieved_at=_NOW),
        )
    )
    built = build_web_research(_registry())
    scripted = built.agent.clone(
        model=ScriptedModel(
            [
                [
                    function_call(
                        "news_search",
                        {"query": "HYPE news", "symbols": ["HYPE"], "since": "week"},
                        call_id="c1",
                    )
                ],
                [assistant_message(_finding_json())],
            ]
        )
    )
    bus = EventBus("sess-web", heartbeat_interval_s=60.0)
    collector = SourceCollector()
    deps = ToolDeps(search=search, bus=bus, sources=collector)
    translator = AgentRunTranslator(bus, agent=AgentName.WEB_RESEARCH, task_id="t1")
    structured = await run_tool_agent(
        scripted,
        web_research_user_message(_task(), now=_NOW),
        strategy=built.strategy,
        deps=deps,
        translator=translator,
        max_turns=4,
    )
    finding = assemble_finding(_task(), structured.output, collector)

    assert finding.sources
    assert finding.sources[0].url == _URL
    assert finding.claims[0].source_ids == [finding.sources[0].id]
    assert finding.summary
    assert search.queries[0].topic.value == "news"

    bus.close()
    events = [event async for event in bus.stream()]
    types = [event.type for event in events]
    assert EventType.TOOL_STARTED in types
    assert EventType.TOOL_COMPLETED in types


async def test_sub_agent_runner_still_placeholders_crypto() -> None:
    runner = SubAgentRunner(
        _registry(),
        limits=IsolatedExecutionLimits(),
        fallback_model_id="deepseek:deepseek-v4-pro",
    )
    bus = EventBus("sess-ph", heartbeat_interval_s=60.0)
    state = ResearchState("sess-ph", "问题", bus=bus)
    task = ResearchTask(id="t1", agent=AgentName.CRYPTO_RESEARCH, objective="获取手续费")
    finding = await runner.run(TaskContext(task=task), state)
    assert finding.data_gaps == [NOT_IMPLEMENTED_GAP]
    assert runner.model_id_for(AgentName.WEB_RESEARCH).startswith("deepseek:")
