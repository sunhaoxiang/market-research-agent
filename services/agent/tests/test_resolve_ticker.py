"""resolve_ticker（P4-3 验收）。

底层只调 `get_ticker_directory`，不包 HTTP。钉死："NVDA" 解析到 CIK
0001045810；同名前缀多条则返回列表让 Agent 选。
"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_service.providers.errors import ProviderError
from agent_service.providers.sec import TickerDirectory, TickerEntry, company_page_url
from agent_service.schemas.tools import DataProvenance, ToolErrorCode
from agent_service.tools.deps import ToolDeps
from agent_service.tools.registry import invoke_tool
from agent_service.tools.stocks.bindings import STOCK_TOOLS
from agent_service.tools.stocks.models import TickerMatchKind
from agent_service.tools.stocks.resolve import disambiguate_tickers, run_resolve_ticker

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_NVDA_CIK = "0001045810"


def _prov() -> DataProvenance:
    return DataProvenance(
        provider="sec_edgar",
        endpoint="/files/company_tickers.json",
        retrieved_at=_NOW,
        is_cached=False,
    )


def _entry(
    ticker: str,
    cik: str,
    title: str,
) -> TickerEntry:
    return TickerEntry(
        cik=cik,
        ticker=ticker,
        title=title,
        url=company_page_url(cik),
    )


_NVDA = _entry("NVDA", _NVDA_CIK, "NVIDIA CORP")
_AAPL = _entry("AAPL", "0000320193", "Apple Inc.")
_APLE = _entry("APLE", "0001418121", "Apple Hospitality REIT, Inc.")
_BRKB = _entry("BRK-B", "0001067983", "BERKSHIRE HATHAWAY INC")
_META = _entry("META", "0001326801", "Meta Platforms, Inc.")


def _directory(*entries: TickerEntry) -> TickerDirectory:
    return TickerDirectory(
        entries=entries or (_NVDA, _AAPL, _APLE, _BRKB, _META),
        url="https://www.sec.gov/search-filings",
        provenance=_prov(),
    )


class FakeSecEdgar:
    def __init__(
        self,
        directory: TickerDirectory | None = None,
        error: ProviderError | None = None,
    ) -> None:
        self.directory = directory
        self.error = error
        self.calls = 0

    async def get_ticker_directory(self) -> TickerDirectory:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.directory is not None
        return self.directory


def test_nvda_ticker_maps_to_cik() -> None:
    decision = disambiguate_tickers("NVDA", _directory().entries)
    assert decision.kind is TickerMatchKind.EXACT_TICKER
    assert decision.resolved is not None
    assert decision.resolved.cik == _NVDA_CIK
    assert decision.resolved.ticker == "NVDA"


def test_brk_dot_b_matches_sec_hyphen() -> None:
    decision = disambiguate_tickers("BRK.B", _directory().entries)
    assert decision.kind is TickerMatchKind.EXACT_TICKER
    assert decision.resolved is not None
    assert decision.resolved.ticker == "BRK-B"


def test_cik_query_maps_to_nvda() -> None:
    decision = disambiguate_tickers("1045810", _directory().entries)
    assert decision.kind is TickerMatchKind.EXACT_CIK
    assert decision.resolved is not None
    assert decision.resolved.ticker == "NVDA"


def test_prefixed_cik_query() -> None:
    decision = disambiguate_tickers("CIK0001045810", _directory().entries)
    assert decision.resolved is not None
    assert decision.resolved.cik == _NVDA_CIK


def test_nvidia_name_is_unique() -> None:
    decision = disambiguate_tickers("NVIDIA", _directory().entries)
    assert decision.kind is TickerMatchKind.UNIQUE_NAME
    assert decision.resolved is not None
    assert decision.resolved.ticker == "NVDA"


def test_apple_name_is_ambiguous() -> None:
    decision = disambiguate_tickers("Apple", _directory().entries)
    assert decision.kind is TickerMatchKind.AMBIGUOUS
    assert decision.resolved is None
    tickers = {item.ticker for item in decision.candidates}
    assert tickers == {"AAPL", "APLE"}


def test_unknown_query_has_no_candidates() -> None:
    decision = disambiguate_tickers("zzzz-not-a-company", _directory().entries)
    assert decision.resolved is None
    assert decision.candidates == ()


async def test_run_nvda_resolves() -> None:
    result = await run_resolve_ticker(
        ToolDeps(sec_edgar=FakeSecEdgar(_directory())), query="  nvda  "
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.resolved is not None
    assert result.data.resolved.cik == _NVDA_CIK
    assert result.data.resolved.ticker == "NVDA"
    assert result.data.resolved.name == "NVIDIA CORP"
    assert result.provenance is not None
    assert result.provenance.source_url == company_page_url("1045810")
    assert result.quality is None


async def test_run_apple_returns_candidates() -> None:
    result = await run_resolve_ticker(ToolDeps(sec_edgar=FakeSecEdgar(_directory())), query="Apple")
    assert result.ok is True
    assert result.data is not None
    assert result.data.resolved is None
    assert {row.ticker for row in result.data.candidates} == {"AAPL", "APLE"}
    assert result.quality is not None
    assert result.quality.missing_fields == ["resolved"]


async def test_run_unknown_is_not_found() -> None:
    result = await run_resolve_ticker(ToolDeps(sec_edgar=FakeSecEdgar(_directory())), query="zzzz")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.NOT_FOUND


async def test_blank_query_is_invalid() -> None:
    result = await run_resolve_ticker(ToolDeps(sec_edgar=FakeSecEdgar(_directory())), query="  ")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_INPUT


async def test_missing_provider_is_unavailable() -> None:
    result = await run_resolve_ticker(ToolDeps(), query="NVDA")
    assert result.ok is False
    assert result.error is not None
    assert result.error.provider == "sec_edgar"


async def test_upstream_error_is_mapped() -> None:
    sec = FakeSecEdgar(
        error=ProviderError(ToolErrorCode.UPSTREAM_ERROR, "SEC 拒绝了请求", provider="sec_edgar")
    )
    result = await run_resolve_ticker(ToolDeps(sec_edgar=sec), query="NVDA")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UPSTREAM_ERROR


async def test_invoke_registry() -> None:
    deps = ToolDeps(sec_edgar=FakeSecEdgar(_directory()))
    ok = await invoke_tool("resolve_ticker", {"query": "NVDA"}, deps)
    assert ok.ok is True
    missing = await invoke_tool("resolve_ticker", {}, deps)
    assert missing.ok is False
    assert missing.error is not None
    assert missing.error.code is ToolErrorCode.INVALID_INPUT


def test_function_tool_name_is_stable() -> None:
    assert STOCK_TOOLS[0].name == "resolve_ticker"
