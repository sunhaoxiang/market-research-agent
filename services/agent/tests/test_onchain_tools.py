"""onchain tools（P3-8 验收）。

Hyperliquid 包现成快照；其它链与 holders/whale/flow 立刻 UNSUPPORTED。
不要打 HTTP、不要把空字段当成 0。
"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_service.providers.errors import ProviderError
from agent_service.providers.onchain import PerpMarketSnapshot
from agent_service.schemas.tools import DataProvenance, ToolErrorCode
from agent_service.tools.deps import ToolDeps
from agent_service.tools.onchain.activity import _COVERAGE, run_get_chain_activity
from agent_service.tools.onchain.bindings import ONCHAIN_TOOLS
from agent_service.tools.onchain.gaps import (
    run_get_exchange_flow,
    run_get_token_holders,
    run_get_whale_activity,
)
from agent_service.tools.registry import invoke_tool

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_PAGE = "https://app.hyperliquid.xyz"
_DAYS_CAVEAT = "Hyperliquid Info API 只有 24h 快照，days 未应用，不要当成历史序列。"


def _prov() -> DataProvenance:
    return DataProvenance(
        provider="hyperliquid",
        endpoint="metaAndAssetCtxs",
        source_url="https://api.hyperliquid.xyz/info",
        retrieved_at=_NOW,
        is_cached=False,
    )


def _snapshot(
    *,
    n_markets: int = 2,
    volume: float | None = 150.0,
    oi: float | None = 46.0,
) -> PerpMarketSnapshot:
    return PerpMarketSnapshot(
        chain="Hyperliquid",
        n_markets=n_markets,
        volume_24h_usd=volume,
        open_interest_usd=oi,
        url=_PAGE,
        provenance=_prov(),
    )


class FakeHyperliquid:
    def __init__(
        self,
        *,
        snapshot: PerpMarketSnapshot | None = None,
        error: ProviderError | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.error = error
        self.calls = 0

    async def get_perp_snapshot(self) -> PerpMarketSnapshot:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.snapshot is not None
        return self.snapshot


async def test_hyperliquid_snapshot_is_always_partial() -> None:
    hl = FakeHyperliquid(snapshot=_snapshot())
    result = await run_get_chain_activity(ToolDeps(hyperliquid=hl), chain="  Hyperliquid  ")
    assert hl.calls == 1
    assert result.ok is True
    assert result.data is not None
    assert result.data.chain == "Hyperliquid"
    assert result.data.days == 1
    assert result.data.n_markets == 2
    assert result.data.volume_24h_usd == 150.0
    assert result.data.open_interest_usd == 46.0
    assert result.data.active_addresses is None
    assert result.data.tx_count is None
    assert result.data.url == _PAGE
    assert result.provenance is not None
    assert result.provenance.source_url == _PAGE
    assert result.provenance.as_of == _NOW
    assert result.quality is not None
    assert result.quality.completeness == "partial"
    assert result.quality.missing_fields == ["active_addresses", "tx_count"]
    assert _COVERAGE in result.quality.caveats


async def test_hyperliquid_l1_alias_is_accepted() -> None:
    hl = FakeHyperliquid(snapshot=_snapshot())
    result = await run_get_chain_activity(ToolDeps(hyperliquid=hl), chain="hyperliquid-l1")
    assert result.ok is True
    assert hl.calls == 1


async def test_other_chain_is_unsupported_and_does_not_call_provider() -> None:
    hl = FakeHyperliquid(snapshot=_snapshot())
    result = await run_get_chain_activity(ToolDeps(hyperliquid=hl), chain="ethereum")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UNSUPPORTED
    assert result.error.provider == "hyperliquid"
    assert hl.calls == 0


async def test_days_not_one_adds_caveat_but_still_returns_snapshot() -> None:
    hl = FakeHyperliquid(snapshot=_snapshot())
    result = await run_get_chain_activity(ToolDeps(hyperliquid=hl), chain="Hyperliquid", days=7)
    assert result.ok is True
    assert result.data is not None
    assert result.data.days == 7
    assert result.data.volume_24h_usd == 150.0
    assert result.quality is not None
    assert _DAYS_CAVEAT in result.quality.caveats
    assert hl.calls == 1


async def test_missing_volume_is_partial_not_zero() -> None:
    hl = FakeHyperliquid(snapshot=_snapshot(volume=None, oi=None))
    result = await run_get_chain_activity(ToolDeps(hyperliquid=hl), chain="Hyperliquid")
    assert result.ok is True
    assert result.data is not None
    assert result.data.volume_24h_usd is None
    assert result.data.open_interest_usd is None
    assert result.quality is not None
    assert result.quality.missing_fields == [
        "volume_24h_usd",
        "open_interest_usd",
        "active_addresses",
        "tx_count",
    ]


async def test_blank_chain_or_bad_days_does_not_call_provider() -> None:
    hl = FakeHyperliquid(snapshot=_snapshot())
    blank = await run_get_chain_activity(ToolDeps(hyperliquid=hl), chain="  ")
    bad = await run_get_chain_activity(ToolDeps(hyperliquid=hl), chain="Hyperliquid", days=0)
    assert blank.ok is False
    assert bad.ok is False
    assert blank.error is not None
    assert blank.error.code is ToolErrorCode.INVALID_INPUT
    assert bad.error is not None
    assert bad.error.code is ToolErrorCode.INVALID_INPUT
    assert hl.calls == 0


async def test_missing_provider_is_unavailable() -> None:
    result = await run_get_chain_activity(ToolDeps(), chain="Hyperliquid")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UPSTREAM_ERROR
    assert result.error.provider == "hyperliquid"


async def test_provider_not_found_becomes_tool_result() -> None:
    hl = FakeHyperliquid(error=ProviderError(ToolErrorCode.NOT_FOUND, "gone", retryable=False))
    result = await run_get_chain_activity(ToolDeps(hyperliquid=hl), chain="Hyperliquid")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.NOT_FOUND


async def test_gap_tools_are_unsupported() -> None:
    holders = await run_get_token_holders(ToolDeps(), asset="hyperliquid")
    whale = await run_get_whale_activity(ToolDeps(), asset="HYPE", threshold=1_000_000)
    flow = await run_get_exchange_flow(ToolDeps(), asset="hyperliquid", days=7)
    assert holders.ok is False
    assert whale.ok is False
    assert flow.ok is False
    assert holders.error is not None
    assert whale.error is not None
    assert flow.error is not None
    assert holders.error.code is ToolErrorCode.UNSUPPORTED
    assert whale.error.code is ToolErrorCode.UNSUPPORTED
    assert flow.error.code is ToolErrorCode.UNSUPPORTED
    assert holders.data is None
    assert whale.data is None
    assert flow.data is None


async def test_gap_tools_reject_blank_asset() -> None:
    holders = await run_get_token_holders(ToolDeps(), asset="  ")
    whale = await run_get_whale_activity(ToolDeps(), asset="")
    flow = await run_get_exchange_flow(ToolDeps(), asset=" ")
    assert holders.error is not None
    assert whale.error is not None
    assert flow.error is not None
    assert holders.error.code is ToolErrorCode.INVALID_INPUT
    assert whale.error.code is ToolErrorCode.INVALID_INPUT
    assert flow.error.code is ToolErrorCode.INVALID_INPUT


async def test_invoke_chain_activity_and_gaps() -> None:
    deps = ToolDeps(hyperliquid=FakeHyperliquid(snapshot=_snapshot()))
    ok = await invoke_tool("get_chain_activity", {"chain": "Hyperliquid"}, deps)
    assert ok.ok is True
    other = await invoke_tool("get_chain_activity", {"chain": "solana"}, deps)
    assert other.ok is False
    assert other.error is not None
    assert other.error.code is ToolErrorCode.UNSUPPORTED
    holders = await invoke_tool("get_token_holders", {"asset": "hyperliquid"}, deps)
    assert holders.ok is False
    missing = await invoke_tool("get_chain_activity", {}, deps)
    assert missing.ok is False
    assert missing.error is not None
    assert missing.error.code is ToolErrorCode.INVALID_INPUT


def test_function_tool_name_is_stable() -> None:
    assert [tool.name for tool in ONCHAIN_TOOLS] == [
        "get_chain_activity",
        "get_token_holders",
        "get_whale_activity",
        "get_exchange_flow",
    ]
