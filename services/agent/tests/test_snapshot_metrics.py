"""超时 salvage 从已成功的 tool 结果抽出对比表数字（P5-3 / D22）。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from agent_service.schemas.tools import DataProvenance, ToolResult
from agent_service.sources.registry import SourceRegistry
from agent_service.tools.crypto.models import CryptoMarketData, CryptoPriceData
from agent_service.tools.defi.models import TvlData, TvlPointData
from agent_service.tools.deps import ToolDeps
from agent_service.tools.financials.models import (
    GrowthMetricsData,
    IncomePeriodData,
    IncomeStatementData,
    ValuationMetricsData,
)
from agent_service.tools.sec.models import EarningsSummaryData
from agent_service.tools.snapshot_metrics import (
    remember_tool_metrics,
    snapshot_metrics_from_tool_data,
)
from agent_service.tools.stocks.models import StockQuoteData
from agent_service.tools.web.collector import SourceCollector, stamp_for_agent

_NOW = datetime(2026, 9, 8, tzinfo=UTC)
_PAGE = "https://www.sec.gov/edgar/browse/?CIK=0001730168"
_Q_END = date(2025, 8, 3)


def _income(*, older: bool = False) -> IncomeStatementData:
    latest = IncomePeriodData(
        period_end=_Q_END,
        fiscal_year=2025,
        fiscal_period="Q3",
        revenue=15_952_000_000.0,
        net_income=4_140_000_000.0,
        eps_diluted=8.72,
        operating_income=5_900_000_000.0,
    )
    rows = [latest]
    if older:
        rows.append(
            IncomePeriodData(
                period_end=date(2025, 5, 4),
                fiscal_year=2025,
                fiscal_period="Q2",
                revenue=15_000_000_000.0,
                net_income=4_000_000_000.0,
                eps_diluted=8.4,
            )
        )
    return IncomeStatementData(
        ticker="AVGO",
        cik="0001730168",
        period="quarterly",
        source="sec_xbrl",
        rows=rows,
        url=_PAGE,
    )


def test_income_statement_latest_row_becomes_snapshot_metrics() -> None:
    points = snapshot_metrics_from_tool_data(_income(older=True), "s1")
    by_name = {item.name: item for item in points}
    assert by_name["revenue"].value == 15_952_000_000.0
    assert by_name["revenue"].entity_symbol == "AVGO"
    assert by_name["revenue"].label == "营收"
    assert by_name["net_income"].value == 4_140_000_000.0
    assert by_name["eps_diluted"].value == 8.72
    assert by_name["revenue"].as_of == datetime(2025, 8, 3, tzinfo=UTC)
    assert {item.source_ref for item in points} == {"s1"}
    assert 15_000_000_000.0 not in {item.value for item in points}


def test_earnings_summary_becomes_snapshot_metrics() -> None:
    data = EarningsSummaryData(
        ticker="AVGO",
        cik="0001730168",
        form="10-Q",
        period_end=_Q_END,
        fiscal_year=2025,
        fiscal_period="Q3",
        revenue=15_952_000_000.0,
        operating_income=5_900_000_000.0,
        net_income=4_140_000_000.0,
        eps_diluted=8.72,
        url=_PAGE,
    )
    points = snapshot_metrics_from_tool_data(data, "s2")
    assert {item.name for item in points} >= {"revenue", "net_income", "eps_diluted"}
    assert points[0].entity_symbol == "AVGO"


def test_valuation_and_quote_use_stable_metric_names() -> None:
    valuation = snapshot_metrics_from_tool_data(
        ValuationMetricsData(
            ticker="NVDA",
            pe=45.2,
            ps=18.4,
            ev_ebitda=23.9,
            url="https://financialmodelingprep.com/financial-summary/NVDA",
        ),
        "s3",
    )
    by_name = {item.name: item for item in valuation}
    assert by_name["pe"].unit == "x"
    assert by_name["ev_ebitda"].value == 23.9

    quote = snapshot_metrics_from_tool_data(
        StockQuoteData(
            ticker="NVDA",
            price=120.5,
            market_cap=3_000_000_000_000.0,
            pe=45.2,
            url="https://financialmodelingprep.com/financial-summary/NVDA",
            as_of=_NOW,
        ),
        "s4",
    )
    names = {item.name for item in quote}
    assert names >= {"price", "market_cap", "pe"}
    assert next(item for item in quote if item.name == "price").unit == "USD"


def test_growth_keeps_yoy_as_decimal() -> None:
    points = snapshot_metrics_from_tool_data(
        GrowthMetricsData(
            ticker="NVDA",
            source="sec_xbrl",
            url=_PAGE,
            as_of=_Q_END,
            revenue_yoy=0.69,
            net_income_yoy=0.54,
        ),
        "s5",
    )
    by_name = {item.name: item for item in points}
    assert by_name["revenue_yoy"].value == 0.69
    assert by_name["revenue_yoy"].unit is None


def test_crypto_market_maps_volume_to_volume_24h() -> None:
    points = snapshot_metrics_from_tool_data(
        CryptoMarketData(
            coin_id="hyperliquid",
            symbol="HYPE",
            name="Hyperliquid",
            vs_currency="usd",
            current_price=42.5,
            market_cap=14_000_000_000.0,
            total_volume=800_000_000.0,
            url="https://www.coingecko.com/en/coins/hyperliquid",
        ),
        "s6",
    )
    by_name = {item.name: item for item in points}
    assert by_name["price"].entity_symbol == "HYPE"
    assert by_name["volume_24h"].value == 800_000_000.0


def test_crypto_price_uses_coin_id() -> None:
    points = snapshot_metrics_from_tool_data(
        CryptoPriceData(
            coin_id="hyperliquid",
            vs_currency="usd",
            price=42.5,
            market_cap=14_000_000_000.0,
            url="https://www.coingecko.com/en/coins/hyperliquid",
        ),
        "s7",
    )
    assert points[0].entity_symbol == "hyperliquid"


def test_missing_fields_are_skipped() -> None:
    points = snapshot_metrics_from_tool_data(
        IncomeStatementData(
            ticker="AVGO",
            period="quarterly",
            source="sec_xbrl",
            rows=[IncomePeriodData(period_end=_Q_END, revenue=15_952_000_000.0)],
            url=_PAGE,
        ),
        "s8",
    )
    assert [item.name for item in points] == ["revenue"]


def test_stamp_for_agent_remembers_income_and_latest_tvl() -> None:
    collector = SourceCollector(registry=SourceRegistry())
    deps = ToolDeps(sources=collector)
    income = stamp_for_agent(
        deps,
        ToolResult.success(
            _income(),
            DataProvenance(
                provider="sec_edgar",
                endpoint="/api/xbrl/companyfacts",
                retrieved_at=_NOW,
            ),
        ),
    )
    assert income.ref == "s1"
    assert any(
        item.name == "revenue" and item.entity_symbol == "AVGO" for item in collector.metrics
    )

    start = _NOW - timedelta(days=2)
    tvl = TvlData(
        scope="protocol",
        protocol="hyperliquid",
        symbol="HYPE",
        tvl_usd=1_500_000_000.0,
        series=[
            TvlPointData(timestamp=start, tvl_usd=1_400_000_000.0),
            TvlPointData(timestamp=_NOW, tvl_usd=1_500_000_000.0),
        ],
        url="https://defillama.com/protocol/hyperliquid",
    )
    remember_tool_metrics(
        collector,
        ToolResult.success(
            tvl,
            DataProvenance(
                provider="defillama",
                endpoint="/protocol/hyperliquid",
                retrieved_at=_NOW,
            ),
        ).model_copy(update={"ref": "s2"}),
    )
    latest = [item for item in collector.metrics if item.name == "tvl"]
    assert len(latest) == 1
    assert latest[0].value == 1_500_000_000.0
