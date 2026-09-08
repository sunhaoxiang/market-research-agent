"""引用完整性校验（P2-9 / §15.4 / §6.3）。纯代码，不调 LLM。"""

from datetime import UTC, datetime

from agent_service.schemas.claims import Claim
from agent_service.schemas.common import ConfidenceLevel, EpistemicType, SourceType
from agent_service.schemas.report import ReportSection, ResearchReport
from agent_service.schemas.sources import Source
from agent_service.sources.citations import assign_citation_indices
from agent_service.sources.guardrail import (
    apply_degradation,
    check_report,
    extract_citation_numbers,
    strip_unknown_citations,
)

_NOW = datetime(2026, 9, 8, tzinfo=UTC)
_URL = "https://theblock.co/hyperliquid-fee-share"


def _source(**overrides: object) -> Source:
    body: dict[str, object] = {
        "ref": "s1",
        "url": _URL,
        "url_canonical": _URL,
        "title": "Fee share",
        "domain": "theblock.co",
        "source_type": SourceType.NEWS,
        "retrieved_at": _NOW,
    }
    body.update(overrides)
    return Source.model_validate(body)


def _claim(source: Source, **overrides: object) -> Claim:
    body: dict[str, object] = {
        "text": "Hyperliquid 正在讨论手续费分享。",
        "epistemic_type": EpistemicType.SOURCE_BACKED_FACT,
        "confidence": ConfidenceLevel.MEDIUM,
        "source_ids": [source.id],
    }
    body.update(overrides)
    return Claim.model_validate(body)


def _report(summary: str, markdown: str = "", *, title: str = "近况") -> ResearchReport:
    return ResearchReport(
        title=title,
        executive_summary=summary,
        sections=[
            ReportSection(id="Overview", title="概述", markdown=markdown or summary),
        ],
    )


def _numbered(source: Source, claim: Claim) -> list[Source]:
    return assign_citation_indices([source], [claim])


def test_extract_skips_markdown_links() -> None:
    text = "详见 [1](https://example.com/a) 与正文 [1]，另见 [2]。"
    assert extract_citation_numbers(text) == (1, 2)


def test_valid_citation_passes() -> None:
    source = _source()
    claim = _claim(source)
    numbered = _numbered(source, claim)
    issues = check_report(_report("讨论手续费分享。[1]"), numbered, [claim])
    assert issues == []


def test_dangling_citation_is_detected() -> None:
    source = _source()
    claim = _claim(source)
    numbered = _numbered(source, claim)
    issues = check_report(_report("讨论手续费分享。[99]"), numbered, [claim])
    assert [item.code for item in issues] == ["dangling_citation"]
    assert "[99]" in issues[0].message
    assert "[1]" in issues[0].message


def test_markdown_link_is_not_a_citation() -> None:
    source = _source()
    claim = _claim(source)
    numbered = _numbered(source, claim)
    issues = check_report(_report("详见 [99](https://example.com/x)。"), numbered, [claim])
    assert issues == []


def test_source_backed_fact_requires_known_source() -> None:
    source = _source()
    missing = _claim(source, source_ids=[])
    unknown = _claim(source, source_ids=["no-such-id"])
    fact = _claim(source, epistemic_type=EpistemicType.FACT, source_ids=[])
    empty = _report("暂无引用。")
    assert [item.code for item in check_report(empty, [source], [missing])] == ["missing_source"]
    assert [item.code for item in check_report(empty, [source], [unknown])] == ["unknown_source"]
    assert check_report(empty, [source], [fact]) == []


def test_http_404_and_non_http_url_are_unavailable() -> None:
    missing = _source(http_status=404)
    bad_url = _source(url="ftp://example.com/a", url_canonical="ftp://example.com/a")
    claim_404 = _claim(missing)
    claim_url = _claim(bad_url)
    numbered_404 = _numbered(missing, claim_404)
    numbered_url = _numbered(bad_url, claim_url)
    report = _report("来源如此。[1]")
    unavailable = [item.code for item in check_report(report, numbered_404, [claim_404])]
    invalid = [item.code for item in check_report(report, numbered_url, [claim_url])]
    assert unavailable == ["unavailable"]
    assert invalid == ["invalid_url"]


def test_investment_advice_is_flagged_but_price_talk_is_not() -> None:
    source = _source()
    claim = _claim(source)
    numbered = _numbered(source, claim)
    flagged = check_report(_report("建议买入该代币。[1]"), numbered, [claim])
    assert [item.code for item in flagged] == ["investment_advice"]
    assert "建议买入" in flagged[0].message

    clean = check_report(
        _report("买方机构给出的买入价高于现价。本文不构成买入建议。[1]"),
        numbered,
        [claim],
    )
    assert clean == []
    advice = check_report(_report("给出买入建议。[1]"), numbered, [claim])
    assert [item.code for item in advice] == ["investment_advice"]


def test_strip_unknown_citations_keeps_valid_numbers() -> None:
    assert strip_unknown_citations("分享讨论。[99] 详见 [1]。", {1}) == "分享讨论。 详见 [1]。"


def test_degradation_strips_dangling_and_records_gap() -> None:
    source = _source()
    claim = _claim(source)
    numbered = _numbered(source, claim)
    draft = _report("讨论手续费分享。[99]", title="近况 [99]")
    issues = check_report(draft, numbered, [claim])
    degraded = apply_degradation(draft, numbered, issues)
    assert "[99]" not in degraded.executive_summary
    assert "[99]" not in degraded.title
    assert any("引用完整性校验未完全通过" in gap for gap in degraded.data_gaps)
    assert any("[99]" in gap for gap in degraded.data_gaps)
