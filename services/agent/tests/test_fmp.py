"""FMP provider 契约测试（P4-1 验收）。

钉死四件事：key 只走 `apikey` 请求头、响应映射（含 200 空 payload /
`Error Message`）、走 BaseProvider 的缓存路径，以及日配额耗尽时
`QUOTA_EXHAUSTED` 快速失败、不打 HTTP。429 / 5xx 重试是基类的职责。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from agent_service.providers.equity import FmpProvider
from agent_service.providers.errors import ProviderError
from agent_service.providers.profiles import ProviderProfile, RetryPolicy
from agent_service.providers.runtime import Clock, ProviderRuntime
from agent_service.schemas.tools import ToolErrorCode

_BASE = "https://financialmodelingprep.com/stable"
_KEY = "fmp-test-key"
_QUOTE_TS = 1_757_318_400  # 2025-09-08T16:00:00Z
_FAST_RETRY = RetryPolicy(attempts=3, initial_wait_s=0.0, max_wait_s=0.0)


@pytest.fixture
async def runtime(tmp_path: Path) -> AsyncIterator[ProviderRuntime]:
    clock = Clock.frozen()

    async def sleep(seconds: float) -> None:
        clock.advance_ms(int(seconds * 1000))

    rt = ProviderRuntime(tmp_path / "cache.db", clock=clock, sleep=sleep)
    await rt.open()
    yield rt
    await rt.aclose()


@asynccontextmanager
async def fmp(
    runtime: ProviderRuntime,
    *,
    api_key: str = _KEY,
    profile: ProviderProfile | None = None,
) -> AsyncIterator[FmpProvider]:
    async with httpx.AsyncClient(base_url=_BASE) as client:
        yield FmpProvider(
            runtime=runtime,
            api_key=api_key,
            client=client,
            profile=profile,
        )


def _quote(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "symbol": "NVDA",
        "name": "NVIDIA Corporation",
        "price": 120.5,
        "change": 2.1,
        "changePercentage": 1.77,
        "volume": 50_000_000,
        "dayLow": 118.0,
        "dayHigh": 122.0,
        "yearLow": 90.0,
        "yearHigh": 140.0,
        "marketCap": 3_000_000_000_000,
        "open": 119.0,
        "previousClose": 118.4,
        "pe": 45.2,
        "eps": 2.66,
        "exchange": "NASDAQ",
        "timestamp": _QUOTE_TS,
    }
    body.update(overrides)
    return body


def _profile(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "symbol": "NVDA",
        "companyName": "NVIDIA Corporation",
        "cik": "0001045810",
        "exchangeShortName": "NASDAQ",
        "industry": "Semiconductors",
        "sector": "Technology",
        "country": "US",
        "currency": "USD",
        "website": "https://www.nvidia.com",
        "ceo": "Jen-Hsun Huang",
        "ipoDate": "1999-01-22",
        "fullTimeEmployees": "29600",
        "marketCap": 3_000_000_000_000,
        "beta": 1.65,
        "isEtf": False,
        "isActivelyTrading": True,
        "description": "NVIDIA designs GPUs.",
    }
    body.update(overrides)
    return body


def _bar(day: str, close: float, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "symbol": "NVDA",
        "date": day,
        "open": close - 1,
        "high": close + 1,
        "low": close - 2,
        "close": close,
        "volume": 40_000_000,
    }
    body.update(overrides)
    return body


def _ratios(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "symbol": "NVDA",
        "priceToEarningsRatioTTM": 45.2,
        "priceToBookRatioTTM": 32.1,
        "priceToSalesRatioTTM": 24.0,
        "enterpriseValueMultipleTTM": 38.5,
        "dividendYieldTTM": 0.03,
    }
    body.update(overrides)
    return body


def _peer(symbol: str, name: str, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "symbol": symbol,
        "companyName": name,
        "price": 100.0,
        "mktCap": 1_000_000_000_000,
    }
    body.update(overrides)
    return body


@respx.mock(base_url=_BASE)
async def test_quote_maps_nvda(respx_mock: respx.MockRouter, runtime: ProviderRuntime) -> None:
    respx_mock.get("/quote").mock(return_value=httpx.Response(200, json=[_quote()]))
    async with fmp(runtime) as provider:
        quote = await provider.get_quote("  nvda  ")

    assert quote.symbol == "NVDA"
    assert quote.name == "NVIDIA Corporation"
    assert quote.price == pytest.approx(120.5)
    assert quote.change_pct == pytest.approx(1.77)
    assert quote.market_cap == pytest.approx(3_000_000_000_000)
    assert quote.as_of == datetime.fromtimestamp(_QUOTE_TS, tz=UTC)
    assert quote.url == "https://financialmodelingprep.com/financial-summary/NVDA"
    assert quote.provenance.provider == "fmp"
    assert quote.provenance.endpoint == "/quote"


@respx.mock(base_url=_BASE)
async def test_request_puts_key_in_header_not_query(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/quote").mock(return_value=httpx.Response(200, json=[_quote()]))
    async with fmp(runtime) as provider:
        await provider.get_quote("NVDA")

    sent = route.calls[0].request
    assert sent.headers["apikey"] == _KEY
    assert _KEY not in str(sent.url)
    assert "apikey" not in str(sent.url).lower()


@respx.mock(base_url=_BASE)
async def test_profile_parses_cik_and_employees(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/profile").mock(return_value=httpx.Response(200, json=[_profile()]))
    async with fmp(runtime) as provider:
        profile = await provider.get_profile("NVDA")

    assert profile.cik == "0001045810"
    assert profile.employees == 29_600
    assert profile.ipo_date == date(1999, 1, 22)
    assert profile.sector == "Technology"
    assert profile.is_etf is False
    assert profile.provenance.endpoint == "/profile"


@respx.mock(base_url=_BASE)
async def test_history_sends_from_to_and_sorts(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/historical-price-eod/full").mock(
        return_value=httpx.Response(
            200,
            json=[_bar("2026-09-08", 120.5), _bar("2026-08-10", 100.0)],
        )
    )
    async with fmp(runtime) as provider:
        history = await provider.get_historical_prices("NVDA", days=30)

    params = dict(route.calls[0].request.url.params)
    assert params["symbol"] == "NVDA"
    assert params["from"] == "2026-08-09"
    assert params["to"] == "2026-09-08"
    assert history.days == 30
    assert [bar.session for bar in history.bars] == [date(2026, 8, 10), date(2026, 9, 8)]
    assert history.bars[0].close == pytest.approx(100.0)
    assert history.provenance.endpoint == "/historical-price-eod/full"


@respx.mock(base_url=_BASE)
async def test_peers_drops_self_and_maps_list(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/stock-peers").mock(
        return_value=httpx.Response(
            200,
            json=[
                _peer("NVDA", "NVIDIA Corporation"),
                _peer("AMD", "Advanced Micro Devices"),
                _peer("AVGO", "Broadcom Inc."),
            ],
        )
    )
    async with fmp(runtime) as provider:
        page = await provider.get_peers("NVDA")

    assert [peer.symbol for peer in page.peers] == ["AMD", "AVGO"]
    assert page.peers[0].url == "https://financialmodelingprep.com/financial-summary/AMD"


@respx.mock(base_url=_BASE)
async def test_peers_list_legacy_format(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/stock-peers").mock(
        return_value=httpx.Response(
            200,
            json=[{"symbol": "NVDA", "peersList": ["AMD", "AVGO", "NVDA"]}],
        )
    )
    async with fmp(runtime) as provider:
        page = await provider.get_peers("NVDA")
    assert [peer.symbol for peer in page.peers] == ["AMD", "AVGO"]


@respx.mock(base_url=_BASE)
async def test_ratios_ttm_maps_aliases(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/ratios-ttm").mock(return_value=httpx.Response(200, json=[_ratios()]))
    async with fmp(runtime) as provider:
        ratios = await provider.get_ratios_ttm("NVDA")

    assert ratios.pe == pytest.approx(45.2)
    assert ratios.pb == pytest.approx(32.1)
    assert ratios.ps == pytest.approx(24.0)
    assert ratios.ev_ebitda == pytest.approx(38.5)
    assert ratios.dividend_yield == pytest.approx(0.03)


@respx.mock(base_url=_BASE)
async def test_missing_ratio_fields_stay_none(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/ratios-ttm").mock(return_value=httpx.Response(200, json=[{"symbol": "NVDA"}]))
    async with fmp(runtime) as provider:
        ratios = await provider.get_ratios_ttm("NVDA")
    assert ratios.pe is None
    assert ratios.pb is None


@respx.mock(base_url=_BASE)
async def test_ratio_history_maps_and_sorts(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/ratios").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "date": "2024-04-28",
                    "fiscalYear": 2025,
                    "period": "Q1",
                    "priceToEarningsRatio": 20.0,
                },
                {
                    "date": "2025-01-26",
                    "fiscalYear": 2025,
                    "period": "FY",
                    "priceToEarningsRatio": 40.0,
                    "priceToBookRatio": 32.0,
                },
            ],
        )
    )
    async with fmp(runtime) as provider:
        page = await provider.get_ratios("nvda", period="quarterly", limit=8)
    assert [row.period_end.isoformat() for row in page.rows] == ["2025-01-26", "2024-04-28"]
    assert page.rows[0].pe == pytest.approx(40.0)
    assert page.rows[0].pb == pytest.approx(32.0)
    assert page.period == "quarterly"
    assert "apikey" not in str(route.calls[0].request.url)


@respx.mock(base_url=_BASE)
async def test_empty_ratio_history_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/ratios").mock(return_value=httpx.Response(200, json=[]))
    async with fmp(runtime) as provider:
        with pytest.raises(ProviderError) as captured:
            await provider.get_ratios("NOPE")
    assert captured.value.code is ToolErrorCode.NOT_FOUND


@respx.mock(base_url=_BASE)
async def test_identical_queries_hit_the_cache(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/quote").mock(return_value=httpx.Response(200, json=[_quote()]))
    async with fmp(runtime) as provider:
        first = await provider.get_quote("nvda")
        second = await provider.get_quote("NVDA")

    assert route.call_count == 1
    assert first.provenance.is_cached is False
    assert second.provenance.is_cached is True


@respx.mock(base_url=_BASE, assert_all_called=False)
async def test_empty_symbol_does_not_call_upstream(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/quote").mock(return_value=httpx.Response(200, json=[_quote()]))
    async with fmp(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_quote("   ")
    assert exc.value.code is ToolErrorCode.INVALID_INPUT
    assert route.call_count == 0


@respx.mock(base_url=_BASE, assert_all_called=False)
async def test_history_days_out_of_range_does_not_call_upstream(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/historical-price-eod/full").mock(
        return_value=httpx.Response(200, json=[_bar("2026-09-08", 120.5)])
    )
    async with fmp(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_historical_prices("NVDA", days=1826)
    assert exc.value.code is ToolErrorCode.INVALID_INPUT
    assert route.call_count == 0


@respx.mock(base_url=_BASE)
async def test_empty_quote_payload_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/quote").mock(return_value=httpx.Response(200, json=[]))
    async with fmp(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_quote("NOPE")
    assert exc.value.code is ToolErrorCode.NOT_FOUND


@respx.mock(base_url=_BASE)
async def test_empty_history_payload_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/historical-price-eod/full").mock(return_value=httpx.Response(200, json=[]))
    async with fmp(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_historical_prices("NOPE")
    assert exc.value.code is ToolErrorCode.NOT_FOUND


@respx.mock(base_url=_BASE)
async def test_http_404_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/quote").mock(return_value=httpx.Response(404, json={"error": "not found"}))
    async with fmp(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_quote("NOPE")
    assert exc.value.code is ToolErrorCode.NOT_FOUND
    assert exc.value.status_code == 404


@respx.mock(base_url=_BASE)
async def test_403_is_a_key_error(respx_mock: respx.MockRouter, runtime: ProviderRuntime) -> None:
    respx_mock.get("/quote").mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
    async with fmp(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_quote("NVDA")
    assert exc.value.status_code == 403
    assert "API key" in exc.value.message


@respx.mock(base_url=_BASE)
async def test_http_402_on_quote_is_quota_exhausted(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    """额度紧张时 `/quote` 也会 402；不能当成可重试的 upstream_error（D21）。"""
    respx_mock.get("/quote").mock(return_value=httpx.Response(402, json={"Error Message": "Limit"}))
    async with fmp(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_quote("AVGO")
    assert exc.value.code is ToolErrorCode.QUOTA_EXHAUSTED
    assert exc.value.status_code == 402
    assert exc.value.retryable is False


@respx.mock(base_url=_BASE)
async def test_http_402_on_ratios_ttm_is_quota_exhausted(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/ratios-ttm").mock(return_value=httpx.Response(402))
    async with fmp(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_ratios_ttm("AVGO")
    assert exc.value.code is ToolErrorCode.QUOTA_EXHAUSTED


@respx.mock(base_url=_BASE)
async def test_http_402_on_ratio_history_is_unsupported(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    """历史 `/ratios` 是付费档；402 应让 Agent 写缺口，而不是搜网页凑分位。"""
    respx_mock.get("/ratios").mock(return_value=httpx.Response(402))
    async with fmp(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_ratios("NVDA", period="quarterly", limit=8)
    assert exc.value.code is ToolErrorCode.UNSUPPORTED
    assert exc.value.status_code == 402


@respx.mock(base_url=_BASE)
async def test_error_message_premium_is_unsupported(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/ratios").mock(
        return_value=httpx.Response(
            200,
            json={"Error Message": "This endpoint is only available for Premium subscribers."},
        )
    )
    async with fmp(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_ratios("NVDA", period="quarterly", limit=8)
    assert exc.value.code is ToolErrorCode.UNSUPPORTED


@respx.mock(base_url=_BASE)
async def test_error_message_limit_reach_is_quota_exhausted(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/quote").mock(
        return_value=httpx.Response(
            200,
            json={"Error Message": "Limit Reach. Please upgrade your plan"},
        )
    )
    async with fmp(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_quote("NVDA")
    assert exc.value.code is ToolErrorCode.QUOTA_EXHAUSTED


@respx.mock(base_url=_BASE)
async def test_error_message_invalid_key(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/quote").mock(
        return_value=httpx.Response(200, json={"Error Message": "Invalid API KEY."})
    )
    async with fmp(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_quote("NVDA")
    assert exc.value.code is ToolErrorCode.UPSTREAM_ERROR
    assert "API key" in exc.value.message


@respx.mock(base_url=_BASE)
async def test_legacy_changes_percentage_alias(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/quote").mock(
        return_value=httpx.Response(
            200,
            json=[_quote(changePercentage=None, changesPercentage=2.5)],
        )
    )
    async with fmp(runtime) as provider:
        quote = await provider.get_quote("NVDA")
    assert quote.change_pct == pytest.approx(2.5)


@respx.mock(base_url=_BASE, assert_all_called=False)
async def test_quota_exhausted_fails_fast_without_http(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/quote").mock(return_value=httpx.Response(200, json=[_quote()]))
    tight = ProviderProfile(rate_per_second=None, burst=5, daily_quota=1, retry=_FAST_RETRY)
    async with fmp(runtime, profile=tight) as provider:
        await provider.get_quote("NVDA")
        with pytest.raises(ProviderError) as exc:
            await provider.get_quote("AAPL")
    assert exc.value.code is ToolErrorCode.QUOTA_EXHAUSTED
    assert route.call_count == 1


@respx.mock(base_url=_BASE)
async def test_cache_hit_does_not_consume_quota(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/quote").mock(return_value=httpx.Response(200, json=[_quote()]))
    tight = ProviderProfile(rate_per_second=None, burst=5, daily_quota=1, retry=_FAST_RETRY)
    async with fmp(runtime, profile=tight) as provider:
        await provider.get_quote("NVDA")
        hit = await provider.get_quote("NVDA")
    assert hit.provenance.is_cached is True
    snap = await runtime.quota.snapshot("fmp", daily_limit=1, monthly_limit=None)
    assert snap.daily_used == 1


@respx.mock(base_url=_BASE)
async def test_income_statement_maps_list(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/income-statement").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "date": "2025-01-26",
                    "symbol": "NVDA",
                    "fiscalYear": 2025,
                    "period": "FY",
                    "revenue": 130_497_000_000,
                    "netIncome": 72_880_000_000,
                    "epsDiluted": 2.94,
                }
            ],
        )
    )
    async with fmp(runtime) as provider:
        page = await provider.get_income_statements("nvda", period="annual", limit=4)
    assert page.rows[0].revenue == pytest.approx(130_497_000_000)
    assert page.rows[0].net_income == pytest.approx(72_880_000_000)
    assert page.rows[0].eps_diluted == pytest.approx(2.94)
    assert page.url == "https://financialmodelingprep.com/financial-summary/NVDA"
    assert "apikey" not in str(route.calls[0].request.url)


@respx.mock(base_url=_BASE)
async def test_empty_income_statement_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/income-statement").mock(return_value=httpx.Response(200, json=[]))
    async with fmp(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_income_statements("NOPE")
    assert exc.value.code is ToolErrorCode.NOT_FOUND


def test_blank_key_is_rejected() -> None:
    with pytest.raises(ProviderError) as exc:
        FmpProvider(runtime=object(), api_key="  ")  # type: ignore[arg-type]
    assert exc.value.code is ToolErrorCode.INVALID_INPUT
