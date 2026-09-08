"""数值冲突检测（P3-10）：多源同指标差异 → CONFLICT_DETECTED，报告并列展示。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_service.orchestrator.conflicts import RELATIVE_TOLERANCE, detect_metric_conflicts
from agent_service.schemas.claims import Claim
from agent_service.schemas.common import AgentName, ConfidenceLevel, EpistemicType, SourceType
from agent_service.schemas.entities import MetricPoint
from agent_service.schemas.findings import ResearchFinding
from agent_service.schemas.sources import Source

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_CG = "https://www.coingecko.com/en/coins/hyperliquid"
_LLAMA = "https://defillama.com/protocol/hyperliquid"


def _source(ref: str, url: str, provider: str) -> Source:
    return Source(
        ref=ref,
        url=url,
        url_canonical=url,
        title=provider,
        domain=url.split("/")[2],
        source_type=SourceType.API,
        provider=provider,
        retrieved_at=_NOW,
    )


def _metric(
    *,
    value: float,
    source_ref: str,
    name: str = "tvl",
    as_of: datetime | None = _NOW,
    entity: str | None = "HYPE",
    unit: str | None = "USD",
) -> MetricPoint:
    return MetricPoint(
        name=name,
        label="TVL",
        value=value,
        unit=unit,
        entity_symbol=entity,
        as_of=as_of,
        source_ref=source_ref,
    )


def _finding(
    sources: list[Source],
    metrics: list[MetricPoint],
    *,
    claims: list[Claim] | None = None,
) -> ResearchFinding:
    return ResearchFinding(
        task_id="t1",
        agent=AgentName.CRYPTO_RESEARCH,
        summary="TVL 来自多个数据源。",
        claims=claims or [],
        sources=sources,
        metrics=metrics,
    )


def test_injected_tvl_conflict_is_detected() -> None:
    """验收：注入冲突数据能被检出，并列列出各源数值与 provider，不取平均。"""
    cg = _source("s1", _CG, "coingecko")
    llama = _source("s2", _LLAMA, "defillama")
    claim = Claim(
        text="HYPE TVL 约 12 亿美元。",
        epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
        confidence=ConfidenceLevel.MEDIUM,
        source_ids=[cg.id],
    )
    conflicts = detect_metric_conflicts(
        [
            _finding(
                [cg, llama],
                [_metric(value=1.2e9, source_ref="s1"), _metric(value=1.8e9, source_ref="s2")],
                claims=[claim],
            )
        ]
    )

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.claim_ids == [claim.id]
    assert "未取平均" in conflict.description
    assert conflict.values == ["coingecko: 1.2e+09 USD", "defillama: 1.8e+09 USD"]
    average = (1.2e9 + 1.8e9) / 2
    assert f"{average:g}" not in "".join(conflict.values)


def test_values_within_tolerance_are_not_conflicts() -> None:
    cg = _source("s1", _CG, "coingecko")
    llama = _source("s2", _LLAMA, "defillama")
    close = 1.2e9 * (1 + RELATIVE_TOLERANCE / 2)
    metrics = [_metric(value=1.2e9, source_ref="s1"), _metric(value=close, source_ref="s2")]
    assert detect_metric_conflicts([_finding([cg, llama], metrics)]) == []


def test_different_as_of_days_are_a_series_not_a_conflict() -> None:
    cg = _source("s1", _CG, "coingecko")
    llama = _source("s2", _LLAMA, "defillama")
    earlier = _NOW - timedelta(days=7)
    assert (
        detect_metric_conflicts(
            [
                _finding(
                    [cg, llama],
                    [
                        _metric(value=1.2e9, source_ref="s1", as_of=earlier),
                        _metric(value=1.8e9, source_ref="s2", as_of=_NOW),
                    ],
                )
            ]
        )
        == []
    )


def test_same_utc_day_still_conflicts() -> None:
    cg = _source("s1", _CG, "coingecko")
    llama = _source("s2", _LLAMA, "defillama")
    later = _NOW + timedelta(hours=3)
    conflicts = detect_metric_conflicts(
        [
            _finding(
                [cg, llama],
                [
                    _metric(value=1.2e9, source_ref="s1", as_of=_NOW),
                    _metric(value=1.8e9, source_ref="s2", as_of=later),
                ],
            )
        ]
    )
    assert len(conflicts) == 1


def test_different_metric_names_do_not_conflict() -> None:
    cg = _source("s1", _CG, "coingecko")
    llama = _source("s2", _LLAMA, "defillama")
    assert (
        detect_metric_conflicts(
            [
                _finding(
                    [cg, llama],
                    [
                        _metric(value=1.2e9, source_ref="s1", name="tvl"),
                        MetricPoint(
                            name="market_cap",
                            label="市值",
                            value=1.8e9,
                            unit="USD",
                            entity_symbol="HYPE",
                            as_of=_NOW,
                            source_ref="s2",
                        ),
                    ],
                )
            ]
        )
        == []
    )


def test_three_sources_become_one_conflict_listing_all_values() -> None:
    cg = _source("s1", _CG, "coingecko")
    llama = _source("s2", _LLAMA, "defillama")
    news = _source("s3", "https://theblock.co/tvl", "theblock")
    conflicts = detect_metric_conflicts(
        [
            _finding(
                [cg, llama, news],
                [
                    _metric(value=1.2e9, source_ref="s1"),
                    _metric(value=1.2e9, source_ref="s2"),
                    _metric(value=1.8e9, source_ref="s3"),
                ],
            )
        ]
    )
    assert len(conflicts) == 1
    assert conflicts[0].values == [
        "coingecko: 1.2e+09 USD",
        "defillama: 1.2e+09 USD",
        "theblock: 1.8e+09 USD",
    ]
