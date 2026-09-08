"""POST /v1/tools/{name}/invoke（P2-4 验收）。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from agent_service.config import get_settings
from agent_service.main import create_app
from agent_service.providers.crypto import CoinPrice, CoinSearchHit, CoinSearchPage
from agent_service.providers.defi import ProtocolTvl, TvlPoint
from agent_service.providers.search import SearchHit, SearchPage
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

    async def get_market(self, coin_id: str, *, vs_currency: str = "usd") -> object:
        raise AssertionError("invoke tests do not call get_market")

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
