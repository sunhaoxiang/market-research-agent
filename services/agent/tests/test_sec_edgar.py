"""SEC EDGAR provider 契约测试（P4-2 验收）。

钉死三件事：每个请求都带合规 `User-Agent`（含邮箱）、CIK 在路径里补成
10 位、以及 submissions / companyfacts 的映射（404 / 空 payload /
ticker 误传入）。429 / 5xx 重试是基类的职责，这里不重复测。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from agent_service.providers.errors import ProviderError
from agent_service.providers.runtime import Clock, ProviderRuntime
from agent_service.providers.sec import (
    SecEdgarProvider,
    company_page_url,
    filing_document_url,
    padded_cik,
)
from agent_service.schemas.tools import ToolErrorCode
from agent_service.testing import IsolatedSettings

_BASE = "https://data.sec.gov"
_UA = "market-research-agent tests@example.com"
_CIK = "0001045810"


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
async def edgar(
    runtime: ProviderRuntime, *, user_agent: str = _UA
) -> AsyncIterator[SecEdgarProvider]:
    async with httpx.AsyncClient(base_url=_BASE) as client:
        yield SecEdgarProvider(runtime=runtime, user_agent=user_agent, client=client)


def _submissions(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "cik": "0001045810",
        "name": "NVIDIA CORP",
        "tickers": ["NVDA"],
        "exchanges": ["Nasdaq"],
        "sic": "3674",
        "sicDescription": "Semiconductors & Related Devices",
        "fiscalYearEnd": "0126",
        "stateOfIncorporation": "DE",
        "filings": {
            "recent": {
                "accessionNumber": ["0001045810-25-000031", "0001045810-25-000012"],
                "filingDate": ["2025-02-26", "2025-05-28"],
                "reportDate": ["2025-01-26", "2025-04-27"],
                "acceptanceDateTime": [
                    "2025-02-26T16:15:12.000Z",
                    "2025-05-28T16:06:00.000Z",
                ],
                "form": ["10-K", "10-Q"],
                "primaryDocument": ["nvda-20250126.htm", "nvda-20250427.htm"],
                "primaryDocDescription": ["10-K", "10-Q"],
                "isXBRL": [1, 1],
            }
        },
    }
    body.update(overrides)
    return body


def _facts(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "cik": 1045810,
        "entityName": "NVIDIA CORP",
        "facts": {
            "dei": {
                "EntityCentralIndexKey": {
                    "label": "Entity Central Index Key",
                    "description": "CIK",
                    "units": {"pure": [{"end": "2025-01-26", "val": 1045810, "form": "10-K"}]},
                }
            },
            "us-gaap": {
                "Revenues": {
                    "label": "Revenues",
                    "description": "Amount of revenue.",
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-29",
                                "end": "2025-01-26",
                                "val": 130_497_000_000,
                                "accn": "0001045810-25-000031",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-02-26",
                            }
                        ]
                    },
                }
            },
        },
    }
    body.update(overrides)
    return body


def test_padded_cik_accepts_short_and_prefixed() -> None:
    assert padded_cik("1045810") == _CIK
    assert padded_cik("  CIK0001045810  ") == _CIK
    assert padded_cik("0001045810") == _CIK


def test_padded_cik_rejects_ticker() -> None:
    with pytest.raises(ProviderError) as exc:
        padded_cik("NVDA")
    assert exc.value.code is ToolErrorCode.INVALID_INPUT


def test_filing_document_url_strips_dashes() -> None:
    url = filing_document_url(
        cik="1045810",
        accession="0001045810-25-000031",
        primary_document="nvda-20250126.htm",
    )
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000031/nvda-20250126.htm"
    )


@respx.mock(base_url=_BASE)
async def test_submissions_maps_nvda(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get(f"/submissions/CIK{_CIK}.json").mock(
        return_value=httpx.Response(200, json=_submissions())
    )
    async with edgar(runtime) as provider:
        page = await provider.get_submissions("1045810")

    assert page.cik == _CIK
    assert page.name == "NVIDIA CORP"
    assert page.tickers == ("NVDA",)
    assert page.sic == "3674"
    assert page.url == company_page_url("1045810")
    assert page.provenance.provider == "sec_edgar"
    ten_k = page.filings_of("10-K")
    assert len(ten_k) == 1
    assert ten_k[0].accession == "0001045810-25-000031"
    assert ten_k[0].filed == date(2025, 2, 26)
    assert ten_k[0].is_xbrl is True
    assert ten_k[0].url.endswith("/nvda-20250126.htm")
    assert ten_k[0].accepted == datetime(2025, 2, 26, 16, 15, 12, tzinfo=UTC)


@respx.mock(base_url=_BASE)
async def test_request_sends_user_agent(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get(f"/submissions/CIK{_CIK}.json").mock(
        return_value=httpx.Response(200, json=_submissions())
    )
    async with edgar(runtime) as provider:
        await provider.get_submissions("1045810")

    sent = route.calls[0].request
    assert sent.headers["user-agent"] == _UA
    assert "python-httpx" not in sent.headers["user-agent"].lower()


@respx.mock(base_url=_BASE)
async def test_cik_is_zero_padded_in_path(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get(f"/submissions/CIK{_CIK}.json").mock(
        return_value=httpx.Response(200, json=_submissions())
    )
    async with edgar(runtime) as provider:
        await provider.get_submissions("1045810")
    assert str(route.calls[0].request.url).endswith(f"/submissions/CIK{_CIK}.json")


@respx.mock(base_url=_BASE)
async def test_company_facts_maps_revenues(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get(f"/api/xbrl/companyfacts/CIK{_CIK}.json").mock(
        return_value=httpx.Response(200, json=_facts())
    )
    async with edgar(runtime) as provider:
        facts = await provider.get_company_facts("cik0001045810")

    assert facts.cik == _CIK
    assert facts.name == "NVIDIA CORP"
    revenues = facts.concept("Revenues")
    assert revenues is not None
    assert revenues.label == "Revenues"
    assert len(revenues.points) == 1
    point = revenues.points[0]
    assert point.value == pytest.approx(130_497_000_000)
    assert point.unit == "USD"
    assert point.fy == 2025
    assert point.fp == "FY"
    assert point.accession == "0001045810-25-000031"
    assert facts.concept("Revenues", taxonomy="dei") is None


@respx.mock(base_url=_BASE)
async def test_identical_queries_hit_the_cache(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get(f"/submissions/CIK{_CIK}.json").mock(
        return_value=httpx.Response(200, json=_submissions())
    )
    async with edgar(runtime) as provider:
        first = await provider.get_submissions("1045810")
        second = await provider.get_submissions("0001045810")

    assert route.call_count == 1
    assert first.provenance.is_cached is False
    assert second.provenance.is_cached is True


@respx.mock(base_url=_BASE, assert_all_called=False)
async def test_ticker_does_not_call_upstream(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get(f"/submissions/CIK{_CIK}.json").mock(
        return_value=httpx.Response(200, json=_submissions())
    )
    async with edgar(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_submissions("NVDA")
    assert exc.value.code is ToolErrorCode.INVALID_INPUT
    assert route.call_count == 0


@respx.mock(base_url=_BASE, assert_all_called=False)
async def test_empty_cik_does_not_call_upstream(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    route = respx_mock.get("/submissions/CIK0000000000.json").mock(
        return_value=httpx.Response(200, json={})
    )
    async with edgar(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_submissions("   ")
    assert exc.value.code is ToolErrorCode.INVALID_INPUT
    assert route.call_count == 0


@respx.mock(base_url=_BASE)
async def test_http_404_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get("/submissions/CIK0000000001.json").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    async with edgar(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_submissions("1")
    assert exc.value.code is ToolErrorCode.NOT_FOUND
    assert exc.value.status_code == 404


@respx.mock(base_url=_BASE)
async def test_403_mentions_user_agent(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get(f"/submissions/CIK{_CIK}.json").mock(
        return_value=httpx.Response(403, text="Forbidden")
    )
    async with edgar(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_submissions("1045810")
    assert exc.value.status_code == 403
    assert "User-Agent" in exc.value.message


@respx.mock(base_url=_BASE)
async def test_missing_facts_key_is_not_found(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get(f"/api/xbrl/companyfacts/CIK{_CIK}.json").mock(
        return_value=httpx.Response(200, json={"cik": 1045810, "entityName": "NVIDIA CORP"})
    )
    async with edgar(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_company_facts("1045810")
    assert exc.value.code is ToolErrorCode.NOT_FOUND


@respx.mock(base_url=_BASE)
async def test_malformed_json_is_parse_error(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    respx_mock.get(f"/submissions/CIK{_CIK}.json").mock(
        return_value=httpx.Response(200, text="not-json")
    )
    async with edgar(runtime) as provider:
        with pytest.raises(ProviderError) as exc:
            await provider.get_submissions("1045810")
    assert exc.value.code is ToolErrorCode.PARSE_ERROR


@respx.mock(base_url=_BASE)
async def test_missing_fact_value_is_dropped_not_zero(
    respx_mock: respx.MockRouter, runtime: ProviderRuntime
) -> None:
    payload = _facts()
    facts = payload["facts"]
    assert isinstance(facts, dict)
    us_gaap = facts["us-gaap"]
    assert isinstance(us_gaap, dict)
    revenues = us_gaap["Revenues"]
    assert isinstance(revenues, dict)
    units = revenues["units"]
    assert isinstance(units, dict)
    units["USD"] = [
        {"end": "2025-01-26", "form": "10-K"},
        {
            "end": "2024-01-28",
            "val": 60_922_000_000,
            "form": "10-K",
            "filed": "2024-02-21",
        },
    ]
    respx_mock.get(f"/api/xbrl/companyfacts/CIK{_CIK}.json").mock(
        return_value=httpx.Response(200, json=payload)
    )
    async with edgar(runtime) as provider:
        page = await provider.get_company_facts("1045810")
    points = page.concept("Revenues")
    assert points is not None
    assert [item.value for item in points.points] == [pytest.approx(60_922_000_000)]


def test_blank_user_agent_is_rejected() -> None:
    with pytest.raises(ProviderError) as exc:
        SecEdgarProvider(runtime=object(), user_agent="   ")  # type: ignore[arg-type]
    assert exc.value.code is ToolErrorCode.INVALID_INPUT


def test_user_agent_without_email_is_rejected() -> None:
    with pytest.raises(ProviderError) as exc:
        SecEdgarProvider(runtime=object(), user_agent="research-bot")  # type: ignore[arg-type]
    assert exc.value.code is ToolErrorCode.INVALID_INPUT


def test_sec_user_agent_accepts_dp_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "App me@example.com")
    settings = IsolatedSettings()
    assert settings.sec_user_agent == "App me@example.com"
