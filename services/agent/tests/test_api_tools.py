"""POST /v1/tools/{name}/invoke（P2-4 验收）。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from agent_service.config import get_settings
from agent_service.main import create_app
from agent_service.providers.crypto import CoinSearchHit, CoinSearchPage
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
    client.app.state.coingecko = _FakeCoinGecko(
        CoinSearchPage(
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
    )
    response = client.post("/v1/tools/resolve_asset/invoke", json={"arguments": {"query": "HYPE"}})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["resolved"]["coin_id"] == "hyperliquid"
    assert body["data"]["resolved"]["name"] == "Hyperliquid"
    assert body["provenance"]["provider"] == "coingecko"


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
