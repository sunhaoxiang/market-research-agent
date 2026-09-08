"""Crypto Research Agent（P3-9）：产出带来源与指标的 ResearchFinding。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from pydantic import SecretStr

from agent_service.agents.crypto_research import (
    CRYPTO_RESEARCH_TOOLS,
    build_crypto_research,
    crypto_research_user_message,
)
from agent_service.agents.findings import (
    EMPTY_CRYPTO_SOURCES_GAP,
    assemble_finding,
    salvage_finding,
)
from agent_service.agents.placeholder import NOT_IMPLEMENTED_GAP
from agent_service.agents.runner import SubAgentRunner
from agent_service.agents.runtime import run_tool_agent
from agent_service.models.registry import ModelRegistry
from agent_service.observability.event_bus import EventBus
from agent_service.observability.sdk_events import AgentRunTranslator
from agent_service.orchestrator.executor import TaskContext
from agent_service.orchestrator.state import ResearchState
from agent_service.providers.crypto import (
    CoinMarket,
    CoinPrice,
    CoinSearchHit,
    CoinSearchPage,
    MarketChart,
    PricePoint,
)
from agent_service.providers.defi import (
    ChainOverview,
    ChainTvl,
    DexVolume,
    FeesRevenue,
    ProtocolTvl,
    TvlPoint,
)
from agent_service.providers.onchain import PerpMarketSnapshot
from agent_service.providers.search import SearchHit, SearchPage, SearchQuery
from agent_service.schemas.common import AgentName, SourceType
from agent_service.schemas.events import EventType
from agent_service.schemas.findings import ResearchFinding
from agent_service.schemas.plan import ResearchTask
from agent_service.schemas.tools import DataProvenance, ToolError, ToolErrorCode
from agent_service.sources.registry import SourceRegistry
from agent_service.testing import (
    IsolatedExecutionLimits,
    IsolatedProviderCredentials,
    IsolatedSettings,
)
from agent_service.tools.deps import ToolDeps
from agent_service.tools.web.collector import SourceCollector

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_CG = "https://www.coingecko.com/en/coins/hyperliquid"
_LLAMA = "https://defillama.com/protocol/hyperliquid"
_NEWS = "https://www.theblock.co/hyperliquid-fee-share"


def _registry() -> ModelRegistry:
    return ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
        )
    )


def _task(objective: str, *, tools: list[str] | None = None) -> ResearchTask:
    return ResearchTask(
        id="t1",
        agent=AgentName.CRYPTO_RESEARCH,
        objective=objective,
        suggested_tools=tools or [],
    )


def _prov(provider: str, endpoint: str) -> DataProvenance:
    return DataProvenance(provider=provider, endpoint=endpoint, retrieved_at=_NOW)


def _market() -> CoinMarket:
    return CoinMarket(
        coin_id="hyperliquid",
        symbol="HYPE",
        name="Hyperliquid",
        vs_currency="usd",
        current_price=42.5,
        market_cap=14_000_000_000.0,
        fully_diluted_valuation=42_500_000_000.0,
        total_volume=200_000_000.0,
        circulating_supply=333_000_000.0,
        total_supply=1_000_000_000.0,
        max_supply=1_000_000_000.0,
        ath=50.0,
        ath_date=_NOW,
        atl=1.0,
        atl_date=_NOW,
        high_24h=44.0,
        low_24h=40.0,
        change_24h_pct=3.2,
        last_updated=_NOW,
        url=_CG,
        provenance=_prov("coingecko", "/coins/markets"),
    )


def _search_page() -> CoinSearchPage:
    return CoinSearchPage(
        query="HYPE",
        hits=(
            CoinSearchHit(
                id="hyperliquid",
                symbol="HYPE",
                name="Hyperliquid",
                market_cap_rank=15,
                url=_CG,
            ),
        ),
        provenance=_prov("coingecko", "/search"),
    )


class FakeCoinGecko:
    def __init__(self) -> None:
        self.search_calls: list[str] = []
        self.market_calls: list[str] = []
        self.chart_calls: list[str] = []

    async def search_coins(self, query: str) -> CoinSearchPage:
        self.search_calls.append(query)
        return _search_page()

    async def get_price(self, coin_id: str, *, vs_currency: str = "usd") -> CoinPrice:
        del vs_currency
        row = _market()
        return CoinPrice(
            coin_id=coin_id,
            vs_currency="usd",
            price=42.5,
            market_cap=row.market_cap,
            volume_24h=row.total_volume,
            change_24h_pct=row.change_24h_pct,
            as_of=_NOW,
            url=_CG,
            provenance=_prov("coingecko", "/simple/price"),
        )

    async def get_market(self, coin_id: str, *, vs_currency: str = "usd") -> CoinMarket:
        del coin_id, vs_currency
        self.market_calls.append("hyperliquid")
        return _market()

    async def get_market_chart(
        self, coin_id: str, *, days: int = 30, vs_currency: str = "usd"
    ) -> MarketChart:
        del vs_currency
        self.chart_calls.append(coin_id)
        return MarketChart(
            coin_id=coin_id,
            vs_currency="usd",
            days=days,
            prices=(
                PricePoint(timestamp=datetime(2026, 8, 9, tzinfo=UTC), price=30.0),
                PricePoint(timestamp=_NOW, price=42.5),
            ),
            url=_CG,
            provenance=_prov("coingecko", f"/coins/{coin_id}/market_chart"),
        )


class FakeDefiLlama:
    def __init__(self) -> None:
        self.protocol_calls: list[str] = []

    async def get_protocol_tvl(self, slug: str, *, days: int = 30) -> ProtocolTvl:
        del days
        self.protocol_calls.append(slug)
        return ProtocolTvl(
            slug=slug,
            name="Hyperliquid",
            symbol="HYPE",
            category="Derivatives",
            chains=("Hyperliquid",),
            tvl_usd=1_500_000_000.0,
            chain_tvls=(("Hyperliquid", 1_500_000_000.0),),
            series=(TvlPoint(timestamp=_NOW, tvl_usd=1_500_000_000.0),),
            url=_LLAMA,
            provenance=_prov("defillama", f"/protocol/{slug}"),
        )

    async def get_chain_tvl(self, chain: str, *, days: int = 30) -> ChainTvl:
        raise AssertionError(chain)

    async def get_fees_revenue(self, slug: str) -> FeesRevenue:
        raise AssertionError(slug)

    async def get_dex_volume(self, slug: str) -> DexVolume:
        raise AssertionError(slug)

    async def get_chain_overview(self, chain: str) -> ChainOverview:
        raise AssertionError(chain)


class FakeHyperliquid:
    async def get_perp_snapshot(self) -> PerpMarketSnapshot:
        return PerpMarketSnapshot(
            chain="Hyperliquid",
            n_markets=2,
            volume_24h_usd=150.0,
            open_interest_usd=46.0,
            url="https://app.hyperliquid.xyz",
            provenance=_prov("hyperliquid", "metaAndAssetCtxs"),
        )


class FakeSearch:
    name = "fake"

    async def search(self, query: SearchQuery) -> SearchPage:
        del query
        return SearchPage(
            query="HYPE",
            hits=(
                SearchHit(
                    url=_NEWS,
                    title="Fee share",
                    snippet="holders",
                    raw_content=None,
                    score=0.9,
                    published_at=_NOW,
                    domain="theblock.co",
                ),
            ),
            provenance=_prov("tavily", "/search"),
        )

    async def aclose(self) -> None:
        return None


def _finding(
    *,
    summary: str,
    text: str,
    metrics: list[dict[str, object]] | None = None,
    gaps: list[str] | None = None,
    source_refs: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "summary": summary,
            "claims": [
                {
                    "text": text,
                    "epistemic_type": "source_backed_fact",
                    "confidence": "high",
                    "source_refs": source_refs or ["s1"],
                }
            ],
            "metrics": metrics or [],
            "data_gaps": gaps or [],
        },
        ensure_ascii=False,
    )


async def _run(
    *,
    objective: str,
    script: ScriptedModel,
    gecko: FakeCoinGecko | None = None,
    llama: FakeDefiLlama | None = None,
    search: FakeSearch | None = None,
) -> tuple[ResearchFinding, SourceCollector, EventBus, FakeCoinGecko, FakeDefiLlama]:
    gecko = gecko or FakeCoinGecko()
    llama = llama or FakeDefiLlama()
    built = build_crypto_research(_registry())
    scripted = built.agent.clone(model=script)
    bus = EventBus("sess-crypto", heartbeat_interval_s=60.0)
    collector = SourceCollector(registry=SourceRegistry(bus=bus))
    deps = ToolDeps(
        search=search,
        coingecko=gecko,
        defillama=llama,
        hyperliquid=FakeHyperliquid(),
        bus=bus,
        sources=collector,
    )
    translator = AgentRunTranslator(bus, agent=AgentName.CRYPTO_RESEARCH, task_id="t1")
    task = _task(objective)
    structured = await run_tool_agent(
        scripted,
        crypto_research_user_message(task, now=_NOW),
        strategy=built.strategy,
        deps=deps,
        translator=translator,
        max_turns=8,
    )
    finding = assemble_finding(
        task, structured.output, collector, empty_sources_gap=EMPTY_CRYPTO_SOURCES_GAP
    )
    return finding, collector, bus, gecko, llama


def test_crypto_prompt_hash_is_stable() -> None:
    first = build_crypto_research(_registry())
    second = build_crypto_research(_registry())
    assert first.prompt.hash == second.prompt.hash
    assert first.prompt.hash != ""
    assert [tool.name for tool in CRYPTO_RESEARCH_TOOLS[:5]] == [
        "resolve_asset",
        "get_crypto_price",
        "get_market_data",
        "get_price_history",
        "get_tokenomics",
    ]


def test_user_message_keeps_date_out_of_system_prompt() -> None:
    text = crypto_research_user_message(_task("介绍一下 HYPE"), now=_NOW)
    assert "2026-09-08" in text
    assert "介绍一下 HYPE" in text
    built = build_crypto_research(_registry())
    assert "2026-09-08" not in str(built.agent.instructions)


async def test_intro_hype_finding_has_market_source_and_metric() -> None:
    """样例 1：介绍一下 HYPE。"""
    finding, _, bus, gecko, _llama = await _run(
        objective="介绍一下 HYPE：市值、供应与定位",
        script=ScriptedModel(
            [
                [function_call("resolve_asset", {"query": "HYPE"}, call_id="c1")],
                [function_call("get_market_data", {"asset": "hyperliquid"}, call_id="c2")],
                [
                    assistant_message(
                        _finding(
                            summary="HYPE 是 Hyperliquid 的原生代币，市值约 140 亿美元。",
                            text="HYPE（Hyperliquid）当前市值约 140 亿美元。",
                            metrics=[
                                {
                                    "name": "market_cap",
                                    "label": "市值",
                                    "value": 14_000_000_000.0,
                                    "unit": "USD",
                                    "entity_symbol": "HYPE",
                                    "source_ref": "s1",
                                }
                            ],
                            gaps=["分配表和解锁日程在免费源上没有"],
                        )
                    )
                ],
            ]
        ),
    )
    assert gecko.search_calls == ["HYPE"]
    assert gecko.market_calls == ["hyperliquid"]
    assert finding.sources
    assert finding.sources[0].url == _CG
    assert finding.sources[0].source_type is SourceType.API
    assert finding.claims[0].source_ids == [finding.sources[0].id]
    assert finding.metrics[0].name == "market_cap"
    assert finding.metrics[0].value == 14_000_000_000.0
    assert any("分配表" in gap for gap in finding.data_gaps)

    bus.close()
    events = [event async for event in bus.stream()]
    types = [event.type for event in events]
    assert EventType.TOOL_STARTED in types
    assert EventType.TOOL_COMPLETED in types
    assert EventType.SOURCE_FOUND in types
    assert EventType.METRIC_FOUND in types


async def test_hype_monthly_move_finding_uses_history() -> None:
    """样例 2：分析 HYPE 最近一个月上涨的原因。"""
    gecko = FakeCoinGecko()
    finding, _collector, bus, gecko, _llama = await _run(
        objective="分析 HYPE 最近一个月上涨的原因，给出区间涨跌幅",
        gecko=gecko,
        search=FakeSearch(),
        script=ScriptedModel(
            [
                [function_call("resolve_asset", {"query": "HYPE"}, call_id="c1")],
                [
                    function_call(
                        "get_price_history",
                        {"asset": "hyperliquid", "days": 30},
                        call_id="c2",
                    )
                ],
                [
                    function_call(
                        "news_search", {"query": "HYPE", "symbols": ["HYPE"]}, call_id="c3"
                    )
                ],
                [
                    assistant_message(
                        _finding(
                            summary="近 30 日 HYPE 从 30 涨到 42.5，同期有手续费分享讨论。",
                            text="近 30 日 HYPE 价格从约 30 美元上涨至 42.5 美元。",
                            metrics=[
                                {
                                    "name": "price",
                                    "label": "价格",
                                    "value": 42.5,
                                    "unit": "USD",
                                    "entity_symbol": "HYPE",
                                    "source_ref": "s1",
                                }
                            ],
                            source_refs=["s1"],
                        )
                    )
                ],
            ]
        ),
    )
    assert gecko.chart_calls == ["hyperliquid"]
    assert finding.sources
    assert {item.url for item in finding.sources} >= {_CG, _NEWS}
    assert finding.claims[0].source_ids
    bus.close()


async def test_tvl_volume_and_flow_gap() -> None:
    """样例 3：查询 HYPE 的 TVL、交易量和资金变化。"""
    llama = FakeDefiLlama()
    finding, _collector, bus, _gecko, llama = await _run(
        objective="查询 HYPE 的 TVL、交易量和资金变化",
        llama=llama,
        script=ScriptedModel(
            [
                [function_call("resolve_asset", {"query": "HYPE"}, call_id="c1")],
                [function_call("get_tvl", {"protocol": "hyperliquid"}, call_id="c2")],
                [function_call("get_exchange_flow", {"asset": "hyperliquid"}, call_id="c3")],
                [
                    assistant_message(
                        _finding(
                            summary="Hyperliquid TVL 约 15 亿美元；交易所净流入没有免费源。",
                            text="Hyperliquid 协议 TVL 约 15 亿美元。",
                            metrics=[
                                {
                                    "name": "tvl",
                                    "label": "TVL",
                                    "value": 1_500_000_000.0,
                                    "unit": "USD",
                                    "entity_symbol": "HYPE",
                                    "source_ref": "s2",
                                }
                            ],
                            gaps=[],
                            source_refs=["s2"],
                        )
                    )
                ],
            ]
        ),
    )
    assert llama.protocol_calls == ["hyperliquid"]
    assert finding.metrics[0].name == "tvl"
    assert finding.metrics[0].value == 1_500_000_000.0
    assert any(item.url == _LLAMA for item in finding.sources)
    assert finding.tool_errors
    assert finding.tool_errors[0].code is ToolErrorCode.UNSUPPORTED
    assert any("交易所" in gap or "净流入" in gap for gap in finding.data_gaps)
    bus.close()


def test_salvage_finding_keeps_sources_and_unsupported_errors() -> None:
    """超时没有 LLM 草稿时，来源仍要能发 [n]，unsupported 必须进 data_gaps。"""
    collector = SourceCollector()
    collector.add(
        url=_LLAMA,
        title="Hyperliquid",
        excerpt=None,
        provider="defillama",
        retrieved_at=_NOW,
        source_type=SourceType.API,
    )
    collector.errors.append(
        ToolError(
            code=ToolErrorCode.UNSUPPORTED,
            message="交易所净流入/流出没有免费 API",
            tool="get_exchange_flow",
        )
    )
    finding = salvage_finding(
        _task("查询资金变化"),
        collector,
        timeout_s=180,
        empty_sources_gap=EMPTY_CRYPTO_SOURCES_GAP,
    )
    assert finding.sources[0].url == _LLAMA
    assert finding.claims[0].source_ids == [finding.sources[0].id]
    assert any("180" in gap for gap in finding.data_gaps)
    assert any("没有免费 API" in gap for gap in finding.data_gaps)
    assert finding.tool_errors[0].code is ToolErrorCode.UNSUPPORTED


async def test_crypto_runner_salvages_collector_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def hang(_agent: object, _user: object, **kwargs: object) -> None:
        deps = kwargs["deps"]
        assert isinstance(deps, ToolDeps)
        sources = deps.sources
        assert sources is not None
        sources.add(
            url=_LLAMA,
            title="Hyperliquid",
            excerpt=None,
            provider="defillama",
            retrieved_at=_NOW,
            source_type=SourceType.API,
        )
        sources.errors.append(
            ToolError(
                code=ToolErrorCode.UNSUPPORTED,
                message="交易所净流入/流出没有免费 API",
                tool="get_exchange_flow",
            )
        )
        await asyncio.Event().wait()

    monkeypatch.setattr("agent_service.agents.runner.run_tool_agent", hang)
    runner = SubAgentRunner(
        _registry(),
        limits=IsolatedExecutionLimits(),
        fallback_model_id="deepseek:deepseek-v4-pro",
    )
    bus = EventBus("sess-salvage", heartbeat_interval_s=60.0)
    state = ResearchState("sess-salvage", "问题", bus=bus)
    context = TaskContext(task=_task("查询资金变化"), timeout_s=180)
    running = asyncio.create_task(runner.run(context, state))
    await asyncio.sleep(0.05)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    saved = context.salvage.finding
    assert saved is not None
    assert saved.sources[0].url == _LLAMA
    assert any("没有免费 API" in gap for gap in saved.data_gaps)


async def test_sub_agent_runner_still_placeholders_fact_checker() -> None:
    runner = SubAgentRunner(
        _registry(),
        limits=IsolatedExecutionLimits(),
        fallback_model_id="deepseek:deepseek-v4-pro",
    )
    bus = EventBus("sess-ph", heartbeat_interval_s=60.0)
    state = ResearchState("sess-ph", "问题", bus=bus)
    task = ResearchTask(id="t1", agent=AgentName.FACT_CHECKER, objective="核对陈述")
    finding = await runner.run(TaskContext(task=task), state)
    assert finding.data_gaps == [NOT_IMPLEMENTED_GAP]
    assert runner.model_id_for(AgentName.CRYPTO_RESEARCH).startswith("deepseek:")
    assert runner.model_id_for(AgentName.WEB_RESEARCH).startswith("deepseek:")
    assert runner.model_id_for(AgentName.STOCK_RESEARCH).startswith("deepseek:")
