"""P7-7：冻结外部数据，同一数据集重复跑分数不变。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_service.providers.errors import ProviderError
from evals.datasets_io import load_jsonl
from evals.external import fixture_deps, load_catalog, replay_calls
from evals.paths import DATASETS_DIR
from evals.runner import EvalConfig, run_eval
from evals.schemas import CaseStatus


def _metric(payload: dict, name: str, entity: str = "") -> float:
    for item in payload.get("metrics") or []:
        if item.get("name") == name and str(item.get("entity") or "") == entity:
            return float(item["value"])
    raise AssertionError(f"missing metric {name} entity={entity!r} in {payload.get('metrics')}")


@pytest.mark.asyncio
async def test_replay_tvl_matches_catalog() -> None:
    metrics, names = await replay_calls(
        [{"name": "get_tvl", "arguments": {"protocol": "hyperliquid"}}]
    )
    assert names == ["get_tvl"]
    assert _metric({"metrics": metrics}, "tvl_usd") == 1_800_000_000.0


@pytest.mark.asyncio
async def test_replay_nvda_pe_and_quarterly_revenue() -> None:
    pe, _ = await replay_calls([{"name": "get_valuation_metrics", "arguments": {"ticker": "NVDA"}}])
    assert _metric({"metrics": pe}, "pe_ttm") == 45.2
    rev, _ = await replay_calls(
        [
            {
                "name": "get_income_statement",
                "arguments": {"ticker": "NVDA", "period": "quarterly"},
            }
        ]
    )
    assert _metric({"metrics": rev}, "revenue_usd") == 44_096_000_000.0


@pytest.mark.asyncio
async def test_missing_fixture_does_not_hit_network() -> None:
    deps = fixture_deps()
    assert getattr(deps.coingecko, "_client", None) is None
    with pytest.raises(ProviderError) as exc:
        await deps.coingecko.get_price("not-a-coin")  # type: ignore[union-attr]
    assert "eval fixture 未收录" in exc.value.message
    with pytest.raises(RuntimeError, match="eval fixture 未收录"):
        await replay_calls([{"name": "get_tvl", "arguments": {"protocol": "missing-proto"}}])


@pytest.mark.asyncio
async def test_report_suites_are_stable_across_runs(tmp_path: Path) -> None:
    first, _ = await run_eval(
        EvalConfig(
            datasets_dir=DATASETS_DIR,
            results_dir=tmp_path / "a",
            suites=("crypto_project", "stock_analysis", "financial_report"),
        )
    )
    second, _ = await run_eval(
        EvalConfig(
            datasets_dir=DATASETS_DIR,
            results_dir=tmp_path / "b",
            suites=("crypto_project", "stock_analysis", "financial_report"),
        )
    )
    assert first.summary.failed == 0
    assert first.summary.errored == 0
    assert second.summary.failed == 0
    assert {item.name: item.value for item in first.metrics} == {
        item.name: item.value for item in second.metrics
    }
    assert all(case.status is CaseStatus.PASS for suite in first.suites for case in suite.cases)


def test_catalog_has_frozen_timestamp() -> None:
    catalog = load_catalog()
    assert catalog["as_of"].endswith("Z")
    assert catalog["pages"]
    assert "hyperliquid" in catalog["protocols"]
    assert "NVDA" in catalog["stocks"]


def test_replay_cases_are_wired_into_report_datasets() -> None:
    n = 0
    for stem in ("crypto_project", "stock_analysis", "financial_report"):
        for case in load_jsonl(DATASETS_DIR / f"{stem}.jsonl"):
            calls = (case.fixtures or {}).get("calls")
            if isinstance(calls, list) and calls:
                n += 1
    assert n >= 30
