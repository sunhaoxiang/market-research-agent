"""POST /v1/tools/{name}/invoke（P2-4 验收）。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from types import MappingProxyType

import pytest
from fastapi.testclient import TestClient

from agent_service.config import get_settings
from agent_service.main import create_app
from agent_service.providers.crypto import CoinMarket, CoinPrice, CoinSearchHit, CoinSearchPage
from agent_service.providers.defi import ProtocolTvl, TvlPoint
from agent_service.providers.equity import (
    BalanceSheets,
    CashFlowStatements,
    IncomeStatements,
    PriceBar,
    StockHistory,
    StockPeer,
    StockPeers,
    StockProfile,
    StockQuote,
)
from agent_service.providers.onchain import PerpMarketSnapshot
from agent_service.providers.search import SearchHit, SearchPage
from agent_service.providers.sec import (
    CompanyFacts,
    FactConcept,
    FactPoint,
    TickerDirectory,
    TickerEntry,
    company_page_url,
)
from agent_service.schemas.tools import DataProvenance, ToolErrorCode

_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "MOONSHOT_API_KEY",
    "DEEPSEEK_API_KEY",
    "ZHIPU_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "TAVILY_API_KEY",
    "INTERNAL_API_TOKEN",
)


class _FakeSearch:
    name = "fake"

    def __init__(self, page: SearchPage) -> None:
        self.page = page

    async def search(self, query: object) -> SearchPage:
        del query
        return self.page

    async def aclose(self) -> None:
        return None


class _FakeCoinGecko:
    def __init__(self, page: CoinSearchPage) -> None:
        self.page = page

    async def search_coins(self, query: str) -> CoinSearchPage:
        del query
        return self.page

    async def get_price(self, coin_id: str, *, vs_currency: str = "usd") -> CoinPrice:
        del vs_currency
        return CoinPrice(
            coin_id=coin_id,
            vs_currency="usd",
            price=42.5,
            market_cap=14_000_000_000.0,
            volume_24h=200_000_000.0,
            change_24h_pct=3.2,
            as_of=datetime(2026, 9, 8, tzinfo=UTC),
            url=f"https://www.coingecko.com/en/coins/{coin_id}",
            provenance=DataProvenance(
                provider="coingecko",
                endpoint="/simple/price",
                retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
            ),
        )

    async def get_market(self, coin_id: str, *, vs_currency: str = "usd") -> CoinMarket:
        del vs_currency
        return CoinMarket(
            coin_id=coin_id,
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
            ath_date=datetime(2026, 9, 8, tzinfo=UTC),
            atl=1.0,
            atl_date=datetime(2026, 9, 8, tzinfo=UTC),
            high_24h=44.0,
            low_24h=40.0,
            change_24h_pct=3.2,
            last_updated=datetime(2026, 9, 8, tzinfo=UTC),
            url=f"https://www.coingecko.com/en/coins/{coin_id}",
            provenance=DataProvenance(
                provider="coingecko",
                endpoint="/coins/markets",
                retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
            ),
        )

    async def get_market_chart(
        self, coin_id: str, *, days: int = 30, vs_currency: str = "usd"
    ) -> object:
        raise AssertionError("invoke tests do not call get_market_chart")

    async def aclose(self) -> None:
        return None


class _FakeDefiLlama:
    async def get_protocol_tvl(self, slug: str, *, days: int = 30) -> ProtocolTvl:
        del days
        return ProtocolTvl(
            slug=slug,
            name="Hyperliquid",
            symbol="HYPE",
            category="Derivatives",
            chains=("Hyperliquid",),
            tvl_usd=1_500_000_000.0,
            chain_tvls=(("Hyperliquid", 1_500_000_000.0),),
            series=(TvlPoint(timestamp=datetime(2026, 9, 8, tzinfo=UTC), tvl_usd=1_500_000_000.0),),
            url=f"https://defillama.com/protocol/{slug}",
            provenance=DataProvenance(
                provider="defillama",
                endpoint=f"/protocol/{slug}",
                retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
            ),
        )

    async def get_chain_tvl(self, chain: str, *, days: int = 30) -> object:
        raise AssertionError("invoke tests do not call get_chain_tvl")

    async def get_fees_revenue(self, slug: str) -> object:
        raise AssertionError("invoke tests do not call get_fees_revenue")

    async def get_dex_volume(self, slug: str) -> object:
        raise AssertionError("invoke tests do not call get_dex_volume")

    async def get_chain_overview(self, chain: str) -> object:
        raise AssertionError("invoke tests do not call get_chain_overview")

    async def aclose(self) -> None:
        return None


class _FakeHyperliquid:
    async def get_perp_snapshot(self) -> PerpMarketSnapshot:
        return PerpMarketSnapshot(
            chain="Hyperliquid",
            n_markets=2,
            volume_24h_usd=150.0,
            open_interest_usd=46.0,
            url="https://app.hyperliquid.xyz",
            provenance=DataProvenance(
                provider="hyperliquid",
                endpoint="metaAndAssetCtxs",
                retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
            ),
        )

    async def aclose(self) -> None:
        return None


class _FakeSecEdgar:
    async def get_ticker_directory(self) -> TickerDirectory:
        return TickerDirectory(
            entries=(
                TickerEntry(
                    cik="0001045810",
                    ticker="NVDA",
                    title="NVIDIA CORP",
                    url=company_page_url("1045810"),
                ),
            ),
            url="https://www.sec.gov/search-filings",
            provenance=DataProvenance(
                provider="sec_edgar",
                endpoint="/files/company_tickers.json",
                retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
            ),
        )

    async def get_company_facts(self, cik: str) -> CompanyFacts:
        del cik
        revenue = FactPoint(
            value=130_497_000_000.0,
            unit="USD",
            start=date(2024, 1, 29),
            end=date(2025, 1, 26),
            filed=date(2025, 2, 26),
            form="10-K",
            fy=2025,
            fp="FY",
            accession="0001045810-25-000031",
            frame=None,
        )
        prior_revenue = FactPoint(
            value=60_922_000_000.0,
            unit="USD",
            start=date(2023, 1, 30),
            end=date(2024, 1, 28),
            filed=date(2024, 2, 21),
            form="10-K",
            fy=2024,
            fp="FY",
            accession="0001045810-24-000031",
            frame=None,
        )
        q2_revenue = FactPoint(
            value=46_743_000_000.0,
            unit="USD",
            start=date(2025, 4, 28),
            end=date(2025, 7, 27),
            filed=date(2025, 8, 27),
            form="10-Q",
            fy=2026,
            fp="Q2",
            accession="0001045810-25-000014",
            frame=None,
        )
        q1_revenue = FactPoint(
            value=44_062_000_000.0,
            unit="USD",
            start=date(2025, 1, 27),
            end=date(2025, 4, 27),
            filed=date(2025, 5, 28),
            form="10-Q",
            fy=2026,
            fp="Q1",
            accession="0001045810-25-000009",
            frame=None,
        )
        assets = FactPoint(
            value=111_601_000_000.0,
            unit="USD",
            start=None,
            end=date(2025, 1, 26),
            filed=date(2025, 2, 26),
            form="10-K",
            fy=2025,
            fp="FY",
            accession="0001045810-25-000031",
            frame=None,
        )
        operating = FactPoint(
            value=64_089_000_000.0,
            unit="USD",
            start=date(2024, 1, 29),
            end=date(2025, 1, 26),
            filed=date(2025, 2, 26),
            form="10-K",
            fy=2025,
            fp="FY",
            accession="0001045810-25-000031",
            frame=None,
        )
        concepts = {
            ("us-gaap", "Revenues"): FactConcept(
                taxonomy="us-gaap",
                tag="Revenues",
                label="Revenues",
                description=None,
                points=(revenue, prior_revenue, q2_revenue, q1_revenue),
            ),
            ("us-gaap", "Assets"): FactConcept(
                taxonomy="us-gaap",
                tag="Assets",
                label="Assets",
                description=None,
                points=(assets,),
            ),
            ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"): FactConcept(
                taxonomy="us-gaap",
                tag="NetCashProvidedByUsedInOperatingActivities",
                label="Operating cash",
                description=None,
                points=(operating,),
            ),
        }
        return CompanyFacts(
            cik="0001045810",
            name="NVIDIA CORP",
            concepts=MappingProxyType(concepts),
            url=company_page_url("1045810"),
            provenance=DataProvenance(
                provider="sec_edgar",
                endpoint="/api/xbrl/companyfacts",
                retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
            ),
        )

    async def aclose(self) -> None:
        return None


class _FakeFmp:
    async def get_quote(self, symbol: str) -> StockQuote:
        return StockQuote(
            symbol=symbol,
            name="NVIDIA Corporation",
            price=120.5,
            change=2.1,
            change_pct=1.77,
            volume=50_000_000.0,
            day_low=118.0,
            day_high=122.0,
            year_low=90.0,
            year_high=140.0,
            market_cap=3_000_000_000_000.0,
            open=119.0,
            previous_close=118.4,
            pe=45.2,
            eps=2.66,
            exchange="NASDAQ",
            as_of=datetime(2026, 9, 8, tzinfo=UTC),
            url=f"https://financialmodelingprep.com/financial-summary/{symbol}",
            provenance=DataProvenance(
                provider="fmp",
                endpoint="/quote",
                retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
            ),
        )

    async def get_profile(self, symbol: str) -> StockProfile:
        return StockProfile(
            symbol=symbol,
            name="NVIDIA Corporation",
            description="GPUs",
            cik="0001045810",
            exchange="NASDAQ",
            industry="Semiconductors",
            sector="Technology",
            country="US",
            currency="USD",
            website="https://www.nvidia.com",
            ceo="Jensen Huang",
            ipo_date=None,
            employees=36_000,
            market_cap=3_000_000_000_000.0,
            beta=1.7,
            is_etf=False,
            is_actively_trading=True,
            url=f"https://financialmodelingprep.com/financial-summary/{symbol}",
            provenance=DataProvenance(
                provider="fmp",
                endpoint="/profile",
                retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
            ),
        )

    async def get_historical_prices(self, symbol: str, *, days: int = 30) -> StockHistory:
        start = date(2026, 8, 10)
        end = date(2026, 9, 8)
        first = 100.0 if symbol == "NVDA" else 400.0
        last = 120.0 if symbol == "NVDA" else 440.0
        return StockHistory(
            symbol=symbol,
            days=days,
            start=start,
            end=end,
            bars=(
                PriceBar(session=start, open=None, high=None, low=None, close=first, volume=None),
                PriceBar(session=end, open=None, high=None, low=None, close=last, volume=None),
            ),
            url=f"https://financialmodelingprep.com/financial-summary/{symbol}",
            provenance=DataProvenance(
                provider="fmp",
                endpoint="/historical-price-eod/full",
                retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
            ),
        )

    async def get_peers(self, symbol: str) -> StockPeers:
        return StockPeers(
            symbol=symbol,
            peers=(
                StockPeer(
                    symbol="AMD",
                    name="Advanced Micro Devices",
                    price=160.0,
                    market_cap=260_000_000_000.0,
                    url="https://financialmodelingprep.com/financial-summary/AMD",
                ),
            ),
            url=f"https://financialmodelingprep.com/financial-summary/{symbol}",
            provenance=DataProvenance(
                provider="fmp",
                endpoint="/stock-peers",
                retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
            ),
        )

    async def get_income_statements(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> IncomeStatements:
        raise AssertionError("invoke 三表应走 SEC XBRL")

    async def get_balance_sheets(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> BalanceSheets:
        raise AssertionError("invoke 三表应走 SEC XBRL")

    async def get_cash_flow_statements(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> CashFlowStatements:
        raise AssertionError("invoke 三表应走 SEC XBRL")

    async def aclose(self) -> None:
        return None


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(isolated_env: None) -> Iterator[TestClient]:
    del isolated_env
    app = create_app()
    with TestClient(app) as test_client:
        app.state.search_provider = _FakeSearch(_page())
        app.state.coingecko = _FakeCoinGecko(_coins())
        app.state.defillama = _FakeDefiLlama()
        app.state.hyperliquid = _FakeHyperliquid()
        app.state.sec_edgar = _FakeSecEdgar()
        app.state.fmp = _FakeFmp()
        yield test_client


def _page() -> SearchPage:
    return SearchPage(
        query="HYPE",
        hits=(
            SearchHit(
                url="https://www.hyperliquid.xyz/docs",
                title="Docs",
                snippet="native token",
                raw_content=None,
                score=0.9,
                published_at=None,
                domain="hyperliquid.xyz",
            ),
        ),
        provenance=DataProvenance(
            provider="tavily",
            endpoint="/search",
            retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
        ),
    )


def _coins() -> CoinSearchPage:
    return CoinSearchPage(
        query="HYPE",
        hits=(
            CoinSearchHit(
                id="hyperliquid",
                symbol="HYPE",
                name="Hyperliquid",
                market_cap_rank=15,
                url="https://www.coingecko.com/en/coins/hyperliquid",
            ),
        ),
        provenance=DataProvenance(
            provider="coingecko",
            endpoint="/search",
            retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
        ),
    )


def test_invoke_web_search(client: TestClient) -> None:
    response = client.post(
        "/v1/tools/web_search/invoke",
        json={"arguments": {"query": "HYPE", "max_results": 3}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["hits"][0]["url"] == "https://www.hyperliquid.xyz/docs"
    assert body["provenance"]["provider"] == "tavily"


def test_invoke_unknown_tool_is_404(client: TestClient) -> None:
    response = client.post("/v1/tools/nope/invoke", json={"arguments": {}})
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "TOOL_NOT_FOUND"


def test_invoke_resolve_asset(client: TestClient) -> None:
    response = client.post("/v1/tools/resolve_asset/invoke", json={"arguments": {"query": "HYPE"}})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["resolved"]["coin_id"] == "hyperliquid"
    assert body["data"]["resolved"]["name"] == "Hyperliquid"
    assert body["provenance"]["provider"] == "coingecko"


def test_invoke_resolve_ticker(client: TestClient) -> None:
    response = client.post("/v1/tools/resolve_ticker/invoke", json={"arguments": {"query": "NVDA"}})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["resolved"]["cik"] == "0001045810"
    assert body["data"]["resolved"]["ticker"] == "NVDA"
    assert body["provenance"]["provider"] == "sec_edgar"


def test_invoke_stock_quote(client: TestClient) -> None:
    response = client.post(
        "/v1/tools/get_stock_quote/invoke",
        json={"arguments": {"ticker": "NVDA"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["ticker"] == "NVDA"
    assert body["data"]["price"] == 120.5
    assert body["provenance"]["source_url"] == (
        "https://financialmodelingprep.com/financial-summary/NVDA"
    )


def test_invoke_company_profile(client: TestClient) -> None:
    response = client.post(
        "/v1/tools/get_company_profile/invoke",
        json={"arguments": {"ticker": "NVDA"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["cik"] == "0001045810"
    assert body["data"]["industry"] == "Semiconductors"


def test_invoke_stock_price_history(client: TestClient) -> None:
    response = client.post(
        "/v1/tools/get_stock_price_history/invoke",
        json={"arguments": {"ticker": "NVDA", "days": 30}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["ticker"] == "NVDA"
    assert body["data"]["bars"][-1]["close"] == 120.0


def test_invoke_peers(client: TestClient) -> None:
    response = client.post("/v1/tools/get_peers/invoke", json={"arguments": {"ticker": "NVDA"}})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["peers"][0]["ticker"] == "AMD"


def test_invoke_compare_to_index(client: TestClient) -> None:
    response = client.post(
        "/v1/tools/compare_to_index/invoke",
        json={"arguments": {"ticker": "NVDA"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["index"] == "SPY"
    assert body["data"]["ticker_return"] == pytest.approx(0.2)
    assert body["data"]["index_return"] == pytest.approx(0.1)
    assert body["data"]["excess_return"] == pytest.approx(0.1)


def test_invoke_income_statement(client: TestClient) -> None:
    response = client.post(
        "/v1/tools/get_income_statement/invoke",
        json={"arguments": {"ticker": "NVDA"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["source"] == "sec_xbrl"
    assert body["data"]["rows"][0]["revenue"] == 130_497_000_000.0
    assert body["provenance"]["source_url"] == "https://www.sec.gov/edgar/browse/?CIK=0001045810"


def test_invoke_balance_sheet(client: TestClient) -> None:
    response = client.post(
        "/v1/tools/get_balance_sheet/invoke",
        json={"arguments": {"ticker": "NVDA"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["rows"][0]["total_assets"] == 111_601_000_000.0


def test_invoke_cash_flow(client: TestClient) -> None:
    response = client.post(
        "/v1/tools/get_cash_flow/invoke",
        json={"arguments": {"ticker": "NVDA"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["rows"][0]["operating"] == 64_089_000_000.0


def test_invoke_growth_metrics(client: TestClient) -> None:
    response = client.post(
        "/v1/tools/get_growth_metrics/invoke",
        json={"arguments": {"ticker": "NVDA"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["revenue_yoy"] == pytest.approx(130_497_000_000.0 / 60_922_000_000.0 - 1.0)
    assert body["data"]["source"] == "sec_xbrl"


def test_invoke_get_tvl(client: TestClient) -> None:
    response = client.post(
        "/v1/tools/get_tvl/invoke",
        json={"arguments": {"protocol": "hyperliquid"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["scope"] == "protocol"
    assert body["data"]["protocol"] == "hyperliquid"
    assert body["data"]["tvl_usd"] == 1_500_000_000.0
    assert body["provenance"]["source_url"] == "https://defillama.com/protocol/hyperliquid"


def test_invoke_get_chain_activity(client: TestClient) -> None:
    response = client.post(
        "/v1/tools/get_chain_activity/invoke",
        json={"arguments": {"chain": "Hyperliquid"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["chain"] == "Hyperliquid"
    assert body["data"]["volume_24h_usd"] == 150.0
    assert body["data"]["open_interest_usd"] == 46.0
    assert body["data"]["active_addresses"] is None
    assert "active_addresses" in body["quality"]["missing_fields"]
    assert body["provenance"]["source_url"] == "https://app.hyperliquid.xyz"


def test_invoke_get_tokenomics(client: TestClient) -> None:
    response = client.post(
        "/v1/tools/get_tokenomics/invoke",
        json={"arguments": {"asset": "hyperliquid"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["coin_id"] == "hyperliquid"
    assert body["data"]["circulating_supply"] == 333_000_000.0
    assert body["data"]["allocations"] == []
    assert body["data"]["unlocks"] == []
    assert "allocations" in body["quality"]["missing_fields"]
    assert "unlocks" in body["quality"]["missing_fields"]
    assert body["provenance"]["source_url"] == "https://www.coingecko.com/en/coins/hyperliquid"


def test_invoke_crypto_price(client: TestClient) -> None:
    response = client.post(
        "/v1/tools/get_crypto_price/invoke",
        json={"arguments": {"asset": "hyperliquid"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["coin_id"] == "hyperliquid"
    assert body["data"]["price"] == 42.5
    assert body["provenance"]["source_url"] == "https://www.coingecko.com/en/coins/hyperliquid"


def test_invoke_invalid_args_are_tool_errors(client: TestClient) -> None:
    """缺参数是 tool 语义，不是 HTTP 422——eval 脚本只需要解析同一种 ToolResult。"""
    response = client.post("/v1/tools/web_search/invoke", json={"arguments": {}})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == ToolErrorCode.INVALID_INPUT.value


def test_invoke_requires_token_when_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "shared-secret")
    get_settings.cache_clear()
    denied = client.post("/v1/tools/web_search/invoke", json={"arguments": {"query": "HYPE"}})
    assert denied.status_code == 401
    allowed = client.post(
        "/v1/tools/web_search/invoke",
        json={"arguments": {"query": "HYPE"}},
        headers={"X-Internal-Token": "shared-secret"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["ok"] is True
