"""Stock Research Agent（P4-10）：产出带来源与指标的 ResearchFinding。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from types import MappingProxyType

import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from pydantic import SecretStr

from agent_service.agents.crypto_research import CRYPTO_RESEARCH_TOOLS
from agent_service.agents.findings import (
    EMPTY_STOCK_SOURCES_GAP,
    assemble_finding,
    salvage_finding,
)
from agent_service.agents.placeholder import NOT_IMPLEMENTED_GAP
from agent_service.agents.runner import SubAgentRunner
from agent_service.agents.runtime import run_tool_agent
from agent_service.agents.stock_research import (
    STOCK_RESEARCH_TOOLS,
    build_stock_research,
    stock_research_user_message,
)
from agent_service.models.registry import ModelRegistry
from agent_service.observability.event_bus import EventBus
from agent_service.observability.sdk_events import AgentRunTranslator
from agent_service.orchestrator.executor import TaskContext
from agent_service.orchestrator.state import ResearchState
from agent_service.providers.equity import (
    BalanceSheets,
    CashFlowStatements,
    IncomeStatements,
    StockHistory,
    StockPeers,
    StockProfile,
    StockQuote,
    ValuationRatioHistory,
    ValuationRatioRow,
    ValuationRatios,
)
from agent_service.providers.sec import (
    CompanyFacts,
    CompanySubmissions,
    FactConcept,
    FactPoint,
    FilingDocument,
    FilingRef,
    TickerDirectory,
    TickerEntry,
    company_page_url,
    filing_document_url,
)
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
_CIK = "0001045810"
_PAGE = company_page_url("1045810")
_FMP = "https://financialmodelingprep.com/financial-summary/NVDA"
_TEN_K = "0001045810-25-000031"
_ANNUAL_REVENUE = 130_497_000_000.0
_Q2_REVENUE = 46_743_000_000.0


def _registry() -> ModelRegistry:
    return ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
        )
    )


def _task(objective: str, *, tools: list[str] | None = None) -> ResearchTask:
    return ResearchTask(
        id="t1",
        agent=AgentName.STOCK_RESEARCH,
        objective=objective,
        suggested_tools=tools or [],
    )


def _prov(provider: str, endpoint: str) -> DataProvenance:
    return DataProvenance(provider=provider, endpoint=endpoint, retrieved_at=_NOW)


def _entry(cik: str, ticker: str, title: str) -> TickerEntry:
    return TickerEntry(cik=cik, ticker=ticker, title=title, url=company_page_url(cik))


def _directory() -> TickerDirectory:
    return TickerDirectory(
        entries=(
            _entry(_CIK, "NVDA", "NVIDIA CORP"),
            _entry("0000002488", "AMD", "ADVANCED MICRO DEVICES INC"),
            _entry("0001730168", "AVGO", "BROADCOM INC"),
        ),
        url="https://www.sec.gov/search-filings",
        provenance=_prov("sec_edgar", "/files/company_tickers.json"),
    )


def _point(
    *,
    value: float,
    start: date | None,
    end: date,
    form: str,
    fy: int,
    fp: str,
    filed: date,
    accession: str,
    unit: str = "USD",
) -> FactPoint:
    return FactPoint(
        value=value,
        unit=unit,
        end=end,
        start=start,
        filed=filed,
        form=form,
        fy=fy,
        fp=fp,
        accession=accession,
        frame=None,
    )


def _annual_point(value: float, *, unit: str = "USD") -> FactPoint:
    return _point(
        value=value,
        start=date(2024, 1, 29),
        end=date(2025, 1, 26),
        form="10-K",
        fy=2025,
        fp="FY",
        filed=date(2025, 2, 26),
        accession=_TEN_K,
        unit=unit,
    )


def _q2_point(value: float, *, unit: str = "USD") -> FactPoint:
    return _point(
        value=value,
        start=date(2025, 4, 28),
        end=date(2025, 7, 27),
        form="10-Q",
        fy=2026,
        fp="Q2",
        filed=date(2025, 8, 27),
        accession="0001045810-25-000014",
        unit=unit,
    )


def _facts() -> CompanyFacts:
    concepts = {
        ("us-gaap", "Revenues"): FactConcept(
            taxonomy="us-gaap",
            tag="Revenues",
            label="Revenues",
            description=None,
            points=(_annual_point(_ANNUAL_REVENUE), _q2_point(_Q2_REVENUE)),
        ),
        ("us-gaap", "OperatingIncomeLoss"): FactConcept(
            taxonomy="us-gaap",
            tag="OperatingIncomeLoss",
            label="Operating income",
            description=None,
            points=(_annual_point(81_453_000_000.0), _q2_point(28_440_000_000.0)),
        ),
        ("us-gaap", "NetIncomeLoss"): FactConcept(
            taxonomy="us-gaap",
            tag="NetIncomeLoss",
            label="Net income",
            description=None,
            points=(_annual_point(72_880_000_000.0), _q2_point(26_422_000_000.0)),
        ),
        ("us-gaap", "EarningsPerShareDiluted"): FactConcept(
            taxonomy="us-gaap",
            tag="EarningsPerShareDiluted",
            label="Diluted EPS",
            description=None,
            points=(
                _annual_point(2.94, unit="USD/shares"),
                _q2_point(1.08, unit="USD/shares"),
            ),
        ),
    }
    return CompanyFacts(
        cik=_CIK,
        name="NVIDIA CORP",
        concepts=MappingProxyType(concepts),
        url=_PAGE,
        provenance=_prov("sec_edgar", "/api/xbrl/companyfacts"),
    )


def _submissions() -> CompanySubmissions:
    return CompanySubmissions(
        cik=_CIK,
        name="NVIDIA CORP",
        tickers=("NVDA",),
        exchanges=("Nasdaq",),
        sic="3674",
        sic_description=None,
        fiscal_year_end="0126",
        state_of_incorporation="DE",
        filings=(
            FilingRef(
                accession=_TEN_K,
                form="10-K",
                filed=date(2025, 2, 26),
                report_date=date(2025, 1, 26),
                accepted=None,
                primary_document="nvda-20250126.htm",
                description="10-K",
                is_xbrl=True,
                url=filing_document_url(
                    cik="1045810",
                    accession=_TEN_K,
                    primary_document="nvda-20250126.htm",
                ),
            ),
        ),
        url=_PAGE,
        provenance=_prov("sec_edgar", "/submissions"),
    )


class FakeSecEdgar:
    def __init__(self) -> None:
        self.directory = _directory()
        self.facts = _facts()
        self.submissions = _submissions()
        self.directory_calls = 0
        self.facts_calls: list[str] = []

    async def get_ticker_directory(self) -> TickerDirectory:
        self.directory_calls += 1
        return self.directory

    async def get_company_facts(self, cik: str) -> CompanyFacts:
        self.facts_calls.append(cik)
        return self.facts

    async def get_submissions(self, cik: str) -> CompanySubmissions:
        del cik
        return self.submissions

    async def get_filing_document(
        self, *, cik: str, accession: str, primary_document: str
    ) -> FilingDocument:
        return FilingDocument(
            cik=_CIK,
            accession=accession,
            primary_document=primary_document,
            html="<html><body><div>ITEM 1. BUSINESS</div><p>GPUs</p></body></html>",
            url=filing_document_url(
                cik=cik, accession=accession, primary_document=primary_document
            ),
            provenance=_prov("sec_edgar", f"/archives/{accession}"),
        )


def _quote(symbol: str, *, price: float, market_cap: float, name: str) -> StockQuote:
    return StockQuote(
        symbol=symbol,
        name=name,
        price=price,
        change=2.1,
        change_pct=1.77,
        volume=50_000_000.0,
        day_low=price - 2,
        day_high=price + 2,
        year_low=price * 0.7,
        year_high=price * 1.2,
        market_cap=market_cap,
        open=price - 1,
        previous_close=price - 2,
        pe=45.2,
        eps=2.66,
        exchange="NASDAQ",
        as_of=_NOW,
        url=f"https://financialmodelingprep.com/financial-summary/{symbol}",
        provenance=_prov("fmp", "/quote"),
    )


def _profile(symbol: str = "NVDA") -> StockProfile:
    return StockProfile(
        symbol=symbol,
        name="NVIDIA Corporation",
        description="We design GPUs for accelerated computing.",
        cik=_CIK,
        exchange="NASDAQ",
        industry="Semiconductors",
        sector="Technology",
        country="US",
        currency="USD",
        website="https://www.nvidia.com",
        ceo="Jensen Huang",
        ipo_date=date(1999, 1, 22),
        employees=36_000,
        market_cap=3_000_000_000_000.0,
        beta=1.7,
        is_etf=False,
        is_actively_trading=True,
        url=_FMP,
        provenance=_prov("fmp", "/profile"),
    )


def _ttm() -> ValuationRatios:
    return ValuationRatios(
        symbol="NVDA",
        pe=45.2,
        pb=32.1,
        ps=24.0,
        ev_ebitda=38.5,
        dividend_yield=None,
        url=_FMP,
        provenance=_prov("fmp", "/ratios-ttm"),
    )


def _ratio_history() -> ValuationRatioHistory:
    return ValuationRatioHistory(
        symbol="NVDA",
        period="quarterly",
        rows=(
            ValuationRatioRow(
                period_end=date(2025, 7, 27),
                fiscal_year=2026,
                fiscal_period="Q2",
                pe=50.0,
                pb=None,
                ps=None,
                ev_ebitda=None,
            ),
            ValuationRatioRow(
                period_end=date(2024, 7, 28),
                fiscal_year=2025,
                fiscal_period="Q2",
                pe=10.0,
                pb=None,
                ps=None,
                ev_ebitda=None,
            ),
        ),
        url=_FMP,
        provenance=_prov("fmp", "/ratios"),
    )


class FakeFmp:
    def __init__(self) -> None:
        self.quote_calls: list[str] = []
        self.profile_calls: list[str] = []
        self.ttm_calls: list[str] = []
        self.history_calls: list[tuple[str, str, int]] = []

    async def get_quote(self, symbol: str) -> StockQuote:
        self.quote_calls.append(symbol)
        quotes = {
            "NVDA": _quote("NVDA", price=120.5, market_cap=3_000_000_000_000.0, name="NVIDIA"),
            "AMD": _quote("AMD", price=160.0, market_cap=260_000_000_000.0, name="AMD"),
            "AVGO": _quote("AVGO", price=300.0, market_cap=1_400_000_000_000.0, name="Broadcom"),
        }
        return quotes[symbol]

    async def get_profile(self, symbol: str) -> StockProfile:
        self.profile_calls.append(symbol)
        return _profile(symbol)

    async def get_historical_prices(self, symbol: str, *, days: int = 30) -> StockHistory:
        del symbol, days
        raise AssertionError("本测试不应打历史价")

    async def get_peers(self, symbol: str) -> StockPeers:
        del symbol
        raise AssertionError("本测试不应打 peers")

    async def get_income_statements(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> IncomeStatements:
        del symbol, period, limit
        raise AssertionError("本测试不应打 FMP 三表")

    async def get_balance_sheets(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> BalanceSheets:
        del symbol, period, limit
        raise AssertionError("本测试不应打 FMP 三表")

    async def get_cash_flow_statements(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> CashFlowStatements:
        del symbol, period, limit
        raise AssertionError("本测试不应打 FMP 三表")

    async def get_ratios_ttm(self, symbol: str) -> ValuationRatios:
        self.ttm_calls.append(symbol)
        return _ttm()

    async def get_ratios(
        self, symbol: str, *, period: str = "quarterly", limit: int = 20
    ) -> ValuationRatioHistory:
        self.history_calls.append((symbol, period, limit))
        return _ratio_history()


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
    sec: FakeSecEdgar | None = None,
    fmp: FakeFmp | None = None,
) -> tuple[ResearchFinding, SourceCollector, EventBus, FakeSecEdgar, FakeFmp]:
    sec = sec or FakeSecEdgar()
    fmp = fmp or FakeFmp()
    built = build_stock_research(_registry())
    scripted = built.agent.clone(model=script)
    bus = EventBus("sess-stock", heartbeat_interval_s=60.0)
    collector = SourceCollector(registry=SourceRegistry(bus=bus))
    deps = ToolDeps(sec_edgar=sec, fmp=fmp, bus=bus, sources=collector)
    translator = AgentRunTranslator(bus, agent=AgentName.STOCK_RESEARCH, task_id="t1")
    task = _task(objective)
    structured = await run_tool_agent(
        scripted,
        stock_research_user_message(task, now=_NOW),
        strategy=built.strategy,
        deps=deps,
        translator=translator,
        max_turns=12,
    )
    finding = assemble_finding(
        task, structured.output, collector, empty_sources_gap=EMPTY_STOCK_SOURCES_GAP
    )
    return finding, collector, bus, sec, fmp


def test_stock_prompt_hash_is_stable() -> None:
    first = build_stock_research(_registry())
    second = build_stock_research(_registry())
    assert first.prompt.hash == second.prompt.hash
    assert first.prompt.hash != ""
    assert [tool.name for tool in STOCK_RESEARCH_TOOLS[:5]] == [
        "resolve_ticker",
        "get_stock_quote",
        "get_company_profile",
        "get_stock_price_history",
        "get_peers",
    ]
    crypto_names = {tool.name for tool in CRYPTO_RESEARCH_TOOLS}
    assert "resolve_ticker" not in crypto_names
    assert "get_earnings_summary" not in crypto_names
    assert "get_valuation_metrics" not in crypto_names


def test_user_message_keeps_date_out_of_system_prompt() -> None:
    text = stock_research_user_message(_task("NVDA 是做什么的"), now=_NOW)
    assert "2026-09-08" in text
    assert "NVDA 是做什么的" in text
    built = build_stock_research(_registry())
    assert "2026-09-08" not in str(built.agent.instructions)
    assert built.agent.model_settings.temperature == 0.3


async def test_nvda_intro_finding_uses_profile() -> None:
    """样例 1：NVDA 是做什么的。"""
    finding, _, bus, sec, fmp = await _run(
        objective="NVDA 是做什么的",
        script=ScriptedModel(
            [
                [function_call("resolve_ticker", {"query": "NVDA"}, call_id="c1")],
                [function_call("get_company_profile", {"ticker": "NVDA"}, call_id="c2")],
                [
                    assistant_message(
                        _finding(
                            summary="NVIDIA 设计 GPU，用于加速计算。",
                            text="NVIDIA 的业务是设计用于加速计算的 GPU。",
                            source_refs=["s2"],
                        )
                    )
                ],
            ]
        ),
    )
    assert sec.directory_calls == 1
    assert fmp.profile_calls == ["NVDA"]
    urls = {item.url for item in finding.sources}
    assert _PAGE in urls
    assert _FMP in urls
    assert finding.claims[0].source_ids
    assert "GPU" in finding.summary
    bus.close()
    events = [event async for event in bus.stream()]
    types = [event.type for event in events]
    assert EventType.TOOL_STARTED in types
    assert EventType.TOOL_COMPLETED in types
    assert EventType.SOURCE_FOUND in types


async def test_nvda_latest_quarter_earnings_finding() -> None:
    """样例 2：分析 NVDA 最近一季财报。有季报时用 Q2，不是 FY 130,497,000,000。"""
    finding, _, bus, sec, _fmp = await _run(
        objective="分析 NVDA 最近一季财报",
        script=ScriptedModel(
            [
                [function_call("resolve_ticker", {"query": "NVDA"}, call_id="c1")],
                [function_call("get_earnings_summary", {"ticker": "NVDA"}, call_id="c2")],
                [
                    assistant_message(
                        _finding(
                            summary="NVDA 最近一季营收约 467 亿美元。",
                            text="NVIDIA 最近一季营收 46,743,000,000 美元。",
                            metrics=[
                                {
                                    "name": "revenue",
                                    "label": "营收",
                                    "value": _Q2_REVENUE,
                                    "unit": "USD",
                                    "entity_symbol": "NVDA",
                                    "source_ref": "s1",
                                }
                            ],
                        )
                    )
                ],
            ]
        ),
    )
    assert sec.facts_calls == [_CIK]
    assert finding.sources
    assert finding.sources[0].source_type is SourceType.API
    assert finding.metrics[0].name == "revenue"
    assert finding.metrics[0].value == _Q2_REVENUE
    bus.close()


async def test_nvda_valuation_finding_uses_percentile() -> None:
    """样例 3：NVDA 估值贵不贵。"""
    finding, _, bus, _sec, fmp = await _run(
        objective="NVDA 估值贵不贵",
        script=ScriptedModel(
            [
                [function_call("resolve_ticker", {"query": "NVDA"}, call_id="c1")],
                [function_call("get_valuation_metrics", {"ticker": "NVDA"}, call_id="c2")],
                [function_call("get_valuation_history", {"ticker": "NVDA"}, call_id="c3")],
                [
                    assistant_message(
                        _finding(
                            summary="NVDA TTM PE 约 45.2，股息率缺失。",
                            text="NVIDIA 当前 TTM PE 为 45.2。",
                            metrics=[
                                {
                                    "name": "pe",
                                    "label": "PE",
                                    "value": 45.2,
                                    "unit": "x",
                                    "entity_symbol": "NVDA",
                                    "source_ref": "s2",
                                }
                            ],
                            gaps=["dividend_yield 在 TTM 里缺失，不能当成 0"],
                            source_refs=["s2"],
                        )
                    )
                ],
            ]
        ),
    )
    assert fmp.ttm_calls == ["NVDA", "NVDA"]
    assert fmp.history_calls == [("NVDA", "quarterly", 20)]
    assert finding.metrics[0].value == pytest.approx(45.2)
    assert any("dividend_yield" in gap for gap in finding.data_gaps)
    bus.close()


async def test_compare_nvda_amd_avgo_finding() -> None:
    """样例 4：比较 NVDA、AMD、AVGO。Agent 层对多 ticker 取数；对比表是 P4-11。"""
    finding, _, bus, sec, fmp = await _run(
        objective="比较 NVDA、AMD、AVGO 的市值",
        script=ScriptedModel(
            [
                [function_call("resolve_ticker", {"query": "NVDA"}, call_id="c1")],
                [function_call("get_stock_quote", {"ticker": "NVDA"}, call_id="c2")],
                [function_call("resolve_ticker", {"query": "AMD"}, call_id="c3")],
                [function_call("get_stock_quote", {"ticker": "AMD"}, call_id="c4")],
                [function_call("resolve_ticker", {"query": "AVGO"}, call_id="c5")],
                [function_call("get_stock_quote", {"ticker": "AVGO"}, call_id="c6")],
                [
                    assistant_message(
                        _finding(
                            summary="三家市值：NVDA 约 3 万亿，AVGO 约 1.4 万亿，AMD 约 2600 亿。",
                            text="NVIDIA 市值约 3 万亿美元，Broadcom 约 1.4 万亿，AMD 约 2600 亿。",
                            metrics=[
                                {
                                    "name": "market_cap",
                                    "label": "市值",
                                    "value": 3_000_000_000_000.0,
                                    "unit": "USD",
                                    "entity_symbol": "NVDA",
                                    "source_ref": "s2",
                                },
                                {
                                    "name": "market_cap",
                                    "label": "市值",
                                    "value": 260_000_000_000.0,
                                    "unit": "USD",
                                    "entity_symbol": "AMD",
                                    "source_ref": "s4",
                                },
                                {
                                    "name": "market_cap",
                                    "label": "市值",
                                    "value": 1_400_000_000_000.0,
                                    "unit": "USD",
                                    "entity_symbol": "AVGO",
                                    "source_ref": "s6",
                                },
                            ],
                            source_refs=["s2", "s4", "s6"],
                        )
                    )
                ],
            ]
        ),
    )
    assert sec.directory_calls == 3
    assert fmp.quote_calls == ["NVDA", "AMD", "AVGO"]
    symbols = {metric.entity_symbol for metric in finding.metrics}
    assert symbols == {"NVDA", "AMD", "AVGO"}
    bus.close()


def test_salvage_finding_keeps_stock_sources() -> None:
    collector = SourceCollector()
    collector.add(
        url=_PAGE,
        title="NVIDIA CORP",
        excerpt=None,
        provider="sec_edgar",
        retrieved_at=_NOW,
        source_type=SourceType.API,
    )
    collector.errors.append(
        ToolError(
            code=ToolErrorCode.NOT_FOUND,
            message="没有可用的利润表期间：NVDA",
            tool="get_earnings_summary",
        )
    )
    finding = salvage_finding(
        _task("分析 NVDA 最近一季财报"),
        collector,
        timeout_s=180,
        empty_sources_gap=EMPTY_STOCK_SOURCES_GAP,
    )
    assert finding.sources[0].url == _PAGE
    assert any("180" in gap for gap in finding.data_gaps)
    assert finding.tool_errors[0].code is ToolErrorCode.NOT_FOUND


async def test_stock_runner_salvages_collector_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def hang(_agent: object, _user: object, **kwargs: object) -> None:
        deps = kwargs["deps"]
        assert isinstance(deps, ToolDeps)
        sources = deps.sources
        assert sources is not None
        sources.add(
            url=_PAGE,
            title="NVIDIA CORP",
            excerpt=None,
            provider="sec_edgar",
            retrieved_at=_NOW,
            source_type=SourceType.API,
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
    context = TaskContext(task=_task("NVDA 是做什么的"), timeout_s=180)
    running = asyncio.create_task(runner.run(context, state))
    await asyncio.sleep(0.05)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    saved = context.salvage.finding
    assert saved is not None
    assert saved.sources[0].url == _PAGE


async def test_sub_agent_runner_placeholders_only_fact_checker() -> None:
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
    assert runner.model_id_for(AgentName.STOCK_RESEARCH).startswith("deepseek:")
    assert runner.model_id_for(AgentName.CRYPTO_RESEARCH).startswith("deepseek:")
    assert runner.model_id_for(AgentName.WEB_RESEARCH).startswith("deepseek:")
