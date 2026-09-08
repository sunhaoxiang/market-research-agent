"""Merge & Dedup（P5-4）：跨 Agent 重复来源合并、陈述折叠、冲突仍汇总。"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_service.observability.event_bus import EventBus
from agent_service.orchestrator.merge import merge_research
from agent_service.orchestrator.state import ResearchState
from agent_service.schemas.claims import Claim
from agent_service.schemas.common import (
    AgentName,
    ConfidenceLevel,
    EpistemicType,
    SourceType,
)
from agent_service.schemas.entities import MetricPoint
from agent_service.schemas.findings import ResearchFinding
from agent_service.schemas.sources import Source
from agent_service.sources.canonical import canonicalize_url

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_URL = "https://www.theblock.co/hyperliquid-fee-share"
_CANONICAL = canonicalize_url(_URL)
_CG = "https://www.coingecko.com/en/coins/hyperliquid"
_LLAMA = "https://defillama.com/protocol/hyperliquid"


def _state() -> ResearchState:
    bus = EventBus("sess-merge", heartbeat_interval_s=60.0)
    return ResearchState("sess-merge", "问题", bus=bus)


def _source(
    ref: str,
    url: str,
    *,
    provider: str = "tavily",
    source_type: SourceType = SourceType.NEWS,
    title: str | None = None,
    excerpt: str | None = None,
) -> Source:
    return Source(
        ref=ref,
        url=url,
        url_canonical=canonicalize_url(url),
        title=title,
        domain=url.split("/")[2].removeprefix("www."),
        source_type=source_type,
        provider=provider,
        retrieved_at=_NOW,
        excerpt=excerpt,
    )


def _claim(text: str, source_ids: list[str], *, epistemic: EpistemicType | None = None) -> Claim:
    return Claim(
        text=text,
        epistemic_type=epistemic or EpistemicType.SOURCE_BACKED_FACT,
        confidence=ConfidenceLevel.MEDIUM,
        source_ids=source_ids,
    )


def _finding(
    task_id: str,
    agent: AgentName,
    sources: list[Source],
    claims: list[Claim],
    *,
    metrics: list[MetricPoint] | None = None,
) -> ResearchFinding:
    return ResearchFinding(
        task_id=task_id,
        agent=agent,
        summary=f"{task_id} 结论",
        claims=claims,
        sources=sources,
        metrics=metrics or [],
    )


def test_duplicate_canonical_sources_across_agents_collapse_to_one() -> None:
    crypto_src = _source("s1", _URL + "?utm_source=twitter", title=None, excerpt="短")
    web_src = _source("s1", _URL + "/", title="Fee share", excerpt="更长的摘录用于悬浮预览")
    assert crypto_src.id != web_src.id

    state = _state()
    state.findings = [
        _finding(
            "t1",
            AgentName.CRYPTO_RESEARCH,
            [crypto_src],
            [_claim("HYPE 正在讨论手续费分成。", [crypto_src.id])],
        ),
        _finding(
            "t2",
            AgentName.WEB_RESEARCH,
            [web_src],
            [_claim("HYPE 正在讨论手续费分成。", [web_src.id])],
        ),
    ]

    merge_research(state)

    catalog = state.source_registry.sources()
    assert len(catalog) == 1
    assert catalog[0].url_canonical == _CANONICAL
    assert catalog[0].title == "Fee share"
    assert catalog[0].excerpt == "更长的摘录用于悬浮预览"

    survivor = catalog[0].id
    crypto, web = state.findings
    assert [item.id for item in crypto.sources] == [survivor]
    assert [item.id for item in web.sources] == [survivor]
    assert crypto.claims[0].source_ids == [survivor]
    assert web.claims == []


def test_distinct_urls_stay_separate_and_refs_do_not_collide() -> None:
    first = _source("s1", _URL)
    second = _source("s1", _CG, provider="coingecko", source_type=SourceType.API, title="HYPE")
    state = _state()
    state.findings = [
        _finding("t1", AgentName.CRYPTO_RESEARCH, [first], [_claim("分成。", [first.id])]),
        _finding("t2", AgentName.WEB_RESEARCH, [second], [_claim("报价。", [second.id])]),
    ]

    merge_research(state)

    catalog = state.source_registry.sources()
    assert [item.url_canonical for item in catalog] == [_CANONICAL, canonicalize_url(_CG)]
    assert [item.ref for item in catalog] == ["s1", "s2"]
    assert state.findings[1].sources[0].ref == "s2"
    assert state.findings[1].claims[0].source_ids == [catalog[1].id]


def test_whitespace_duplicate_claims_merge_source_ids() -> None:
    a = _source("s1", _URL)
    b = _source("s2", _CG, provider="coingecko", source_type=SourceType.API)
    state = _state()
    state.findings = [
        _finding(
            "t1",
            AgentName.CRYPTO_RESEARCH,
            [a],
            [_claim("HYPE TVL 约 15 亿美元。", [a.id], epistemic=EpistemicType.FACT)],
        ),
        _finding(
            "t2",
            AgentName.WEB_RESEARCH,
            [b],
            [_claim("  HYPE   TVL 约 15 亿美元。 ", [b.id])],
        ),
    ]

    merge_research(state)

    kept = state.findings[0].claims[0]
    assert kept.epistemic_type is EpistemicType.SOURCE_BACKED_FACT
    assert set(kept.source_ids) == {a.id, b.id}
    assert state.findings[1].claims == []


def test_analysis_is_not_folded_into_a_fact() -> None:
    src = _source("s1", _URL)
    state = _state()
    state.findings = [
        _finding(
            "t1",
            AgentName.CRYPTO_RESEARCH,
            [src],
            [_claim("手续费分成会推高代币需求。", [src.id])],
        ),
        _finding(
            "t2",
            AgentName.WEB_RESEARCH,
            [src],
            [
                Claim(
                    text="手续费分成会推高代币需求。",
                    epistemic_type=EpistemicType.ANALYSIS,
                    confidence=ConfidenceLevel.LOW,
                    source_ids=[src.id],
                )
            ],
        ),
    ]

    merge_research(state)

    assert len(state.findings[0].claims) == 1
    assert state.findings[0].claims[0].epistemic_type is EpistemicType.SOURCE_BACKED_FACT
    assert len(state.findings[1].claims) == 1
    assert state.findings[1].claims[0].epistemic_type is EpistemicType.ANALYSIS


def test_metric_conflicts_are_still_summarized() -> None:
    cg = _source("s1", _CG, provider="coingecko", source_type=SourceType.API)
    llama = _source("s2", _LLAMA, provider="defillama", source_type=SourceType.API)
    state = _state()
    state.findings = [
        _finding(
            "t1",
            AgentName.CRYPTO_RESEARCH,
            [cg, llama],
            [],
            metrics=[
                MetricPoint(
                    name="tvl",
                    label="TVL",
                    value=1.2e9,
                    unit="USD",
                    entity_symbol="HYPE",
                    as_of=_NOW,
                    source_ref="s1",
                ),
                MetricPoint(
                    name="tvl",
                    label="TVL",
                    value=1.8e9,
                    unit="USD",
                    entity_symbol="HYPE",
                    as_of=_NOW,
                    source_ref="s2",
                ),
            ],
        )
    ]

    merge_research(state)

    assert len(state.conflicts) == 1
    assert state.conflicts[0].values == ["coingecko: 1.2e+09 USD", "defillama: 1.8e+09 USD"]
