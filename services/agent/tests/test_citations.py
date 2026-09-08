"""报告引用编号（P2-8 / §15.4-3）。"""

from datetime import UTC, datetime

from agent_service.schemas.claims import Claim
from agent_service.schemas.common import ConfidenceLevel, EpistemicType, SourceType
from agent_service.schemas.sources import Source
from agent_service.sources.citations import assign_citation_indices, bibliography

_NOW = datetime(2026, 9, 8, tzinfo=UTC)


def _source(ref: str, url: str) -> Source:
    return Source(
        ref=ref,
        url=url,
        url_canonical=url,
        source_type=SourceType.NEWS,
        retrieved_at=_NOW,
    )


def test_only_cited_sources_get_numbers() -> None:
    used = _source("s1", "https://theblock.co/a")
    orphan = _source("s2", "https://example.com/blog")
    claim = Claim(
        text="手续费分享在讨论中。",
        epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
        confidence=ConfidenceLevel.MEDIUM,
        source_ids=[used.id],
    )
    numbered = assign_citation_indices([used, orphan], [claim])
    assert numbered[0].citation_index == 1
    assert numbered[1].citation_index is None
    cited = bibliography(numbered)
    assert [item.ref for item in cited] == ["s1"]
    assert cited[0].citation_index == 1


def test_discovery_order_becomes_citation_order() -> None:
    first = _source("s1", "https://sec.gov/a")
    second = _source("s2", "https://theblock.co/a")
    claims = [
        Claim(
            text="二",
            epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
            confidence=ConfidenceLevel.HIGH,
            source_ids=[second.id],
        ),
        Claim(
            text="一",
            epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
            confidence=ConfidenceLevel.HIGH,
            source_ids=[first.id],
        ),
    ]
    numbered = assign_citation_indices([first, second], claims)
    assert [item.citation_index for item in numbered] == [1, 2]
