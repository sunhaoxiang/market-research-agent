"""GET /v1/debug/providers（P6-9）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_service.config import get_settings
from agent_service.main import create_app
from agent_service.providers.base import ProviderStats

_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "MOONSHOT_API_KEY",
    "DEEPSEEK_API_KEY",
    "ZHIPU_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "INTERNAL_API_TOKEN",
    "FMP_API_KEY",
    "TAVILY_API_KEY",
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for name in _KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_lists_known_providers(client: TestClient) -> None:
    body = client.get("/v1/debug/providers").json()
    names = [row["provider"] for row in body["providers"]]
    assert names == [
        "tavily",
        "web_fetch",
        "coingecko",
        "defillama",
        "hyperliquid",
        "fmp",
        "sec_edgar",
    ]
    defillama = next(row for row in body["providers"] if row["provider"] == "defillama")
    assert defillama["configured"] is True
    assert defillama["requests"] == 0
    assert defillama["cache_hit_rate"] is None
    fmp = next(row for row in body["providers"] if row["provider"] == "fmp")
    assert fmp["daily_limit"] == 250


def test_exposes_in_memory_stats(client: TestClient) -> None:
    app = client.app
    assert isinstance(app, FastAPI)
    app.state.defillama.stats = ProviderStats(
        requests=10, cache_hits=7, cache_misses=3, http_attempts=3, errors=1
    )
    body = client.get("/v1/debug/providers").json()
    row = next(item for item in body["providers"] if item["provider"] == "defillama")
    assert row["cache_hits"] == 7
    assert row["cache_hit_rate"] == pytest.approx(0.7)
    assert row["errors"] == 1


def test_does_not_leak_secrets(client: TestClient) -> None:
    raw = client.get("/v1/debug/providers").text.lower()
    for forbidden in ("sk-", "api_key", "secret"):
        assert forbidden not in raw
